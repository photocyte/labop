# Core packages
import collections
import copy
import logging
import typing as tp

# 3rd party packages
import xarray as xr


# Project packages
import uml
from labop import ActivityNodeExecution, SampleMap
from labop.data import deserialize_sample_format, serialize_sample_format
from labop.primitive_execution import input_parameter_map
from labop.strings import Strings


class SampleProvenanceObserver:
    """
    Tracks sample provenance over time, forming a directed graph.

    Samples are implicitly tracked: what aliquots are combined for a given
    operation at t-1 determine the provenance for the aliquots (samples) at t.

    Supported operations are:

    - TransferByMap
    """

    def __init__(self) -> None:
        self.graph = None

        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.DEBUG)
        self.exec_tick = 0
        self.handlers = {
            "https://bioprotocols.org/labop/primitives/liquid_handling/TransferByMap": TransferByMapUpdater,
            "https://bioprotocols.org/labop/primitives/sample_arrays/EmptyContainer": EmptyContainerUpdater,
        }

    def update(self, record: ActivityNodeExecution):
        call = record.call.lookup()
        behavior = record.node.lookup().behavior.lookup()
        inputs = [
            x
            for x in call.parameter_values
            if x.parameter.lookup().property_value.direction == uml.PARAMETER_IN
        ]
        iparams = input_parameter_map(inputs)

        if behavior.identity in self.handlers:
            updater = self.handlers[behavior.identity](self.graph, self.exec_tick)
            new_nodes = updater.update(iparams)
            if self.graph:
                self.graph = xr.concat([new_nodes, self.graph], dim="tick")
            else:
                self.graph = new_nodes
            self.exec_tick = updater.exec_tick
        else:
            self.logger.info(
                "Behavior %s is not handled by %s, skipping ...",
                behavior,
                self.__class__,
            )

        # self.draw()

    def metadata(self, sources: xr.DataArray, tick: int) -> xr.Dataset:
        """Return metadata about aliquots at the specified tick.

        Really just a graph indexing operation.

        Assumption: sources contains an 'aliquot' dimension which has the
                    aliquots of interest.

        Assumption: All aliquots of interest have the same types of contents. If
                    an aliquot doesn't have a given content type, then the
                    corresponding amount is 0.

        """
        if tick < 0 or tick > self.exec_tick:
            raise RuntimeError(f"Bad tick: Must be [0, {self.exec_tick}]")

        matches = [
            x
            for x, y in self.graph.nodes(data=True)
            if y["tick"] == tick and y[Strings.SAMPLE] in sources["target_aliquot"]
        ]

        content_types = list(self.graph.nodes[matches[0]][Strings.CONTENTS].keys())
        return xr.DataArray(
            [
                [
                    self.graph.nodes[node_idx][Strings.CONTENTS][c]
                    for c in self.graph.nodes[node_idx][Strings.CONTENTS]
                ]
                for node_idx in matches
            ],
            dims=[Strings.SAMPLE, Strings.CONTENTS],
            coords={
                Strings.SAMPLE: sources[Strings.SAMPLE].data,
                Strings.CONTENTS: content_types,
            },
        )

    def draw(self) -> None:
        """
        Draw the provenance graph in its current state.

        Mainly for debugging purposes.
        """
        import matplotlib.pyplot as plt

        pos = nx.multipartite_layout(self.graph, subset_key="layer")
        nx.draw(
            self.graph,
            pos,
            with_labels=True,
            labels={n: self.graph.nodes[n]["label"] for n in self.graph.nodes},
        )

        edge_labels = {
            (u, v): data["label"] for u, v, data in self.graph.edges(data=True)
        }
        nx.draw_networkx_edge_labels(self.graph, pos, edge_labels=edge_labels)
        plt.show()


class BaseUpdater:
    def __init__(self, graph: tp.Optional[xr.Dataset], exec_tick: int) -> None:
        self.graph = graph
        self.exec_tick = exec_tick
        self.logger = logging.getLogger(__name__)

    def create_sample_nodes(self, sarr: xr.Dataset) -> xr.Dataset:
        nodes = []
        for loc in sarr[Strings.LOCATION]:
            if tracked := self.sample_tracked(loc, self.exec_tick):
                self.logger.debug(
                    "Aliquot=%d already tracked", tracked.location.data.item()
                )
                nodes.append(tracked)
                continue

            # For a hash, use the aliquot ID+parents list. The parents list is
            # an empty set since we are creating a new node.
            idx = hash(f"sample_{loc}" + str(self.exec_tick))
            contents = sarr[Strings.CONTENTS].sel({Strings.LOCATION: loc})
            self.logger.debug("Add aliquot %d=%s", idx, contents)
            uuid = xr.DataArray(
                [[idx]],
                dims=["location", "tick"],
                coords={"location": [loc.data], "tick": [self.exec_tick]},
            )
            nodes.append(xr.merge([{"UUID": uuid}, contents]))

        return xr.concat(nodes, dim="location")

    def sample_tracked(
        self, sample: xr.DataArray, tick: int
    ) -> tp.Optional[xr.Dataset]:
        if self.graph is None:
            return None

        # Can't do these with compound conditions--raises errors if there is
        # more than 1 tick
        match = self.graph.where(
            self.graph.tick == tick,
            drop=True,
        ).where(self.graph.location == sample.location, drop=True)

        # Check any of the variables in the dataset to see if they are empty,
        # meaning no such sample is tracked.
        if match.UUID.size == 0:
            return None

        return match


class EmptyContainerUpdater(BaseUpdater):
    """
    Updater sample states as a result of an EmptyContainer operation.

    This creates new samples that correspond to the initial_contents of the SampleArray
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

    def update(self, iparams: dict) -> xr.Dataset:
        # Since this is a no-op, nothing to do but create the nodes for each
        # sample in the sample array.
        graph = self.create_sample_nodes(iparams["sample_array"].to_data_array())
        self.exec_tick += 1

        return graph


class TransferByMapUpdater(BaseUpdater):
    """
    Updater sample states as a result of a TransferByMap operation.

    Each source aliquot has the specified amount removed and put in ALL
    target aliquots, so the resulting graph is bipartite between one tick and
    the next.

    Assumption: all types of 'contents' appear in all source aliquots.

    Flow:
    - Add source aliquots to graph
    - Add target aliquots to graph
    - Add edges from each source aliquot to all target aliquots
    - Flow contents along edges from sources to targets
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

    def update(self, iparams: dict) -> xr.Dataset:
        source_array = iparams["source"].to_data_array()
        target_array = iparams["destination"].to_data_array()
        source_name = iparams["source"].name
        target_name = iparams["destination"].name
        current_source_nodes = self.create_sample_nodes(source_array)
        current_target_nodes = self.create_sample_nodes(target_array)

        transfer_plan = iparams["plan"].get_map()

        # Modify plan to refer to source_array and target_array
        source_containers = list(set(source_array[Strings.CONTAINER].data))
        target_containers = list(set(target_array[Strings.CONTAINER].data))
        assert len(source_containers) == 1
        assert len(target_containers) == 1
        transfer_plan.coords["source_container"] = transfer_plan.coords[
            "source_container"
        ].where(False, source_containers[0])
        transfer_plan.coords["target_container"] = transfer_plan.coords[
            "target_container"
        ].where(False, target_containers[0])

        # Rename array locations and containers to align with transfer plan
        source_array = source_array.rename(
            {
                Strings.LOCATION: f"{source_name}_location",
                Strings.CONTAINER: f"{source_name}_container",
            }
        )
        target_array = target_array.rename(
            {
                Strings.LOCATION: f"{target_name}_location",
                Strings.CONTAINER: f"{target_name}_container",
            }
        )

        # Get concentration of the aliquot contents
        source_array[
            Strings.CONCENTRATION
        ] = source_array.contents / source_array.contents.sum(dim=Strings.REAGENT)

        # Get amount of each aliquot's contents that is transferred
        amount_transferred = source_array.concentration * transfer_plan

        # Get total amount transferred to all targets
        next_source_contents = source_array.contents - amount_transferred.sum(
            dim=["target_location", "target_container"]
        )
        next_source_contents = next_source_contents.rename(
            {
                f"{source_name}_location": Strings.LOCATION,
                f"{source_name}_container": Strings.CONTAINER,
            }
        )
        next_target_contents = target_array.contents + amount_transferred.sum(
            dim=["source_location", "source_container"]
        )
        next_target_contents = next_target_contents.rename(
            {
                f"{target_name}_location": Strings.LOCATION,
                f"{target_name}_container": Strings.CONTAINER,
            }
        )

        next_source_array = xr.Dataset(
            {
                "sample_location": xr.DataArray(
                    [
                        [
                            f"{source_array.sample_location.sel(source_container=c, source_location=loc).data}_new"
                            for loc in source_array.source_location
                        ]
                        for c in source_array.source_container
                    ],
                    dims=(Strings.CONTAINER, Strings.LOCATION),
                ),
                "contents": next_source_contents,
            },
            coords={
                Strings.CONTAINER: source_array.coords["source_container"].data,
                Strings.LOCATION: source_array.coords["source_location"].data,
            },
        )
        next_target_array = xr.Dataset(
            {
                "sample_location": xr.DataArray(
                    [
                        [
                            f"{target_array.sample_location.sel(target_container=c, target_location=loc).data}_new"
                            for loc in target_array.target_location
                        ]
                        for c in target_array.target_container
                    ],
                    dims=(Strings.CONTAINER, Strings.LOCATION),
                ),
                "contents": next_target_contents,
            },
            coords={
                Strings.CONTAINER: target_array.coords["target_container"].data,
                Strings.LOCATION: target_array.coords["target_location"].data,
            },
        )
        next_source_nodes = self.create_sample_nodes(next_source_array)

        # For nicer visual representations of graphs, the space is split into
        # two before/after spaces by using different ticks: one for source
        # aliquots and one for target aliquots.
        self.exec_tick += 1

        next_target_nodes = self.create_sample_nodes(next_target_array)

        # One-to-one connectivity between the (now) old source nodes and the new
        # ones: same contents, minus what was transferred out. Same for old/new
        # target nodes.
        connectivity1 = xr.Dataset(
            {
                "connectivity": xr.DataArray(
                    [
                        [l1 in l2 for l1 in current_source_nodes.location.data]
                        for l2 in next_source_nodes.location.data
                    ],
                    dims=["source", "target"],
                    coords={
                        "source": current_source_nodes.location.rename(
                            {"location": "source"}
                        ),
                        "target": next_source_nodes.location.rename(
                            {"location": "target"}
                        ),
                    },
                )
            }
        )
        current_source_nodes.update(connectivity1)

        connectivity2 = xr.Dataset(
            {
                "connectivity": xr.DataArray(
                    [
                        [l1 in l2 for l1 in current_target_nodes.location.data]
                        for l2 in next_target_nodes.location.data
                    ],
                    dims=["source", "target"],
                    coords={
                        "source": current_target_nodes.location.rename(
                            {"location": "source"}
                        ),
                        "target": next_target_nodes.location.rename(
                            {"location": "target"}
                        ),
                    },
                )
            }
        )
        current_target_nodes.update(connectivity2)

        # One-to-many connectivity from each source node to all target nodes.
        connectivity3 = xr.Dataset(
            {
                "connectivity": xr.DataArray(
                    [
                        [True for l2 in next_target_nodes.location.data]
                        for l1 in next_source_nodes.location
                    ],
                    coords={
                        "source": current_source_nodes.location.rename(
                            {"location": "source"}
                        ),
                        "target": next_target_nodes.location.rename(
                            {"location": "target"}
                        ),
                    },
                )
            }
        )
        current_source_nodes.update(connectivity3)

        all_nodes = xr.concat(
            [
                next_source_nodes,
                next_target_nodes,
            ],
            dim="tick",
        )

        self.exec_tick += 1
        return all_nodes
