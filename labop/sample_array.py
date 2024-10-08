"""
The SampleArray class defines the functions corresponding to the dynamically generated labop class SampleArray
"""

import json
import logging
import os

import xarray as xr

import labop.inner as inner

from .container_spec import ContainerSpec
from .data import deserialize_sample_format, serialize_sample_format
from .sample_collection import SampleCollection
from .sample_mask import SampleMask
from .strings import Strings
from .utils.plate_coordinates import contiguous_coordinates, get_sample_list

l = logging.Logger(__file__)
l.setLevel(logging.INFO)


class SampleArray(inner.SampleArray, SampleCollection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def get_sample_names(self):
        sample_dict = json.loads(self.initial_contents)
        return [self.document.find(s).name for s in sample_dict.keys()]

    def get_sample(self, sample_name):
        sample_dict = json.loads(self.initial_contents)
        samples = [self.document.find(s) for s in sample_dict.keys()]
        for s in samples:
            if s.name == sample_name:
                return self.document.find(s)
        raise LookupError(
            f"SampleArray does not contain initial contents named {sample_name}"
        )

    def new_sample_id(self) -> str:
        if not hasattr(self, "sample_counter"):
            self.sample_counter = 0

        id = self.sample_counter
        self.sample_counter += 1
        return f"{Strings.SAMPLE}_{self.name}_{id}"

    def empty(self, geometry=None, sample_format=Strings.XARRAY):
        locations = get_sample_list(geometry) if geometry else []
        samples = [self.new_sample_id() for l in locations]

        if sample_format == Strings.XARRAY:
            sample_array = xr.Dataset(
                {
                    Strings.CONTENTS: xr.DataArray(
                        [[[]] * len(locations)],
                        dims=(
                            Strings.CONTAINER,
                            Strings.LOCATION,
                            Strings.REAGENT,
                        ),
                    ),
                    Strings.SAMPLE_LOCATION: xr.DataArray(
                        [samples],
                        dims=(Strings.CONTAINER, Strings.LOCATION),
                    ),
                },
                coords={
                    Strings.SAMPLE: samples,
                    Strings.REAGENT: [],
                    Strings.CONTAINER: [self.container_type],
                    Strings.LOCATION: locations,
                },
            )
        elif sample_format == Strings.JSON:
            sample_array = {s: None for s in locations}
        else:
            raise Exception(
                f"Cannot initialize contents of sample_format: {sample_format}"
            )
        self.initial_contents = serialize_sample_format(sample_array)
        return sample_array

    def to_data_array(self, sample_format=Strings.XARRAY, order=Strings.ROW_DIRECTION):
        if not hasattr(self, "initial_contents") or self.initial_contents is None:
            sample_array = self.empty(sample_format=sample_format)
        else:
            sample_array = deserialize_sample_format(self.initial_contents, parent=self)
        return sample_array

    def mask(self, mask, sample_format=Strings.XARRAY):
        """
        Create a mask array out of SampleArray and mask.
        """
        if sample_format == Strings.JSON:
            return mask
        elif sample_format == Strings.XARRAY:
            initial_contents_array = self.to_data_array(sample_format=sample_format)
            if isinstance(mask, SampleMask):
                masked_array = mask.to_data_array(sample_format=sample_format)
            else:
                mask_coordinates = get_sample_list(mask)
                # Mask the data variables
                mask_array = initial_contents_array.where(
                    initial_contents_array.location.isin(mask_coordinates),
                    drop=True,
                )
                # Remove unused coordinates
                masked_array = mask_array.assign_coords(
                    {
                        Strings.SAMPLE: mask_array.sample.where(
                            mask_array.sample.isin(mask_array.sample_location),
                            drop=True,
                        )
                    }
                )
            return masked_array
        else:
            raise Exception(
                f"Sample format {self.sample_format} is not currently supported by the mask method"
            )

    def from_dict(self, initial_contents: dict):
        if self.sample_format == "json":
            self.initial_contents = serialize_sample_format(initial_contents)
        else:
            raise Exception(
                "Failed to write initial contents. The SampleArray initial contents are not configured for json format"
            )
        return self.initial_contents

    def to_dict(self, sample_format=Strings.XARRAY) -> dict:
        if sample_format == Strings.JSON:
            if not self.initial_contents:
                return {}
            if self.initial_contents == "https://github.com/synbiodex/pysbol3#missing":
                return {}
            # De-serialize the initial_contents field of a SampleArray
            return deserialize_sample_format(self.initial_contents)
        else:
            raise Exception(
                "Failed to dump initial contents to dict. The SampleArray initial contents do not appear to be json format"
            )

    def get_coordinates(self, sample_format=Strings.XARRAY):
        if sample_format == Strings.JSON:
            return self.to_dict(Strings.JSON).values()
        elif sample_format == Strings.XARRAY:
            initial_contents = self.to_data_array()
            return [c for c in initial_contents[Strings.LOCATION].data]
        else:
            raise ValueError(f"Unsupported sample format: {self.sample_format}")

    def from_coordinates(
        source: SampleCollection,
        coordinates: str,
        sample_type=Strings.XARRAY,
    ):
        mask = SampleMask(source=source)
        mask_array = source.mask(coordinates)
        mask_array.name = mask.identity
        mask.mask = serialize_sample_format(mask_array)
        return mask

    def from_container_spec(
        container_type: ContainerSpec, sample_format=Strings.XARRAY
    ):
        sample_array = SampleArray(container_type=container_type)

        if (
            hasattr(container_type, "queryString")
            and container_type.queryString is not None
            and container_type.queryString
            == "cont:Opentrons24TubeRackwithEppendorf1.5mLSafe-LockSnapcap"
        ):
            geometry = "A1:C8"
        elif (
            hasattr(container_type, "queryString")
            and container_type.queryString is not None
            and (
                container_type.queryString.startswith("cont:StockReagent")
                or container_type.queryString == "cont:WasteContainer"
            )
        ):
            geometry = "A1"
        else:
            geometry = "A1:H12"

        initial_contents = sample_array.empty(
            geometry=geometry, sample_format=sample_format
        )
        sample_array.initial_contents = serialize_sample_format(initial_contents)
        sample_array.container_type = container_type
        return sample_array

    def plot(self, out_dir="out"):
        try:
            import matplotlib.pyplot as plt

            sa = self.to_data_array()
            # p = sa.plot.scatter(col="aliquot", x="contents")
            p = sa.plot()
            name = self.name if (hasattr(self, "name") and self.name) else self.identity
            plt.savefig(f"{os.path.join(out_dir, name)}.pdf")
            return p
        except Exception as e:
            l.warning(
                "Could not import matplotlib.  Install matplotlib to plot SampleArray objects."
            )
            return None

    def sample_coordinates(self, sample_format=Strings.XARRAY, as_list=False):
        sample_array = deserialize_sample_format(self.initial_contents, parent=self)
        if sample_format == Strings.XARRAY:
            coords = sample_array.coords[Strings.LOCATION].data.tolist()
            return contiguous_coordinates(coords) if not as_list else coords
        else:
            return sample_array

    def __str__(self):
        """
        Create a human readable string for a SampleArray.
        :param self:
        :return: str
        """
        return f"SampleArray(name={self.name}, container_type={self.container_type}, initial_contents={self.initial_contents})"

    def __repr__(self):
        """
        Create a human readable string for a SampleArray.
        :param self:
        :return: str
        """
        return self.__str__()

    def plot(self):
        """
        Render the sample array using a matplotlib plot
        """
        self.plot()

    def get_container_type(self) -> ContainerSpec:
        return self.container_type.lookup()
