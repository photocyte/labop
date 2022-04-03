import logging
import json
import os 
from typing import Union

import sbol3
import tyto

import paml
import uml
from paml_convert.behavior_specialization import BehaviorSpecialization
from paml_convert.markdown import MarkdownConverter


l = logging.getLogger(__file__)
l.setLevel(logging.ERROR)

container_ontology_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), '../../container-ontology/owl/container-ontology.ttl')
ContainerOntology = tyto.Ontology(path=container_ontology_path, uri='https://sift.net/container-ontology/container-ontology')


class MarkdownSpecialization(BehaviorSpecialization):
    def __init__(self, out_path) -> None:
        super().__init__()
        self.out_path = out_path
        self.markdown = ""
        self.var_to_entity = {}
        self.markdown_converter = None
        self.markdown_steps = []
        self.doc = None

    def _init_behavior_func_map(self) -> dict:
        return {
            "https://bioprotocols.org/paml/primitives/sample_arrays/EmptyContainer": self.define_container,
            "https://bioprotocols.org/paml/primitives/liquid_handling/Provision": self.provision_container,
            "https://bioprotocols.org/paml/primitives/sample_arrays/PlateCoordinates": self.plate_coordinates,
            "https://bioprotocols.org/paml/primitives/spectrophotometry/MeasureAbsorbance": self.measure_absorbance,
            "https://bioprotocols.org/paml/primitives/spectrophotometry/MeasureFluorescence": self.measure_fluorescence,
            "https://bioprotocols.org/paml/primitives/liquid_handling/Vortex": self.vortex,
            "https://bioprotocols.org/paml/primitives/liquid_handling/Discard": self.discard,
            "https://bioprotocols.org/paml/primitives/liquid_handling/Transfer": self.transfer,
            "https://bioprotocols.org/paml/primitives/culturing/Transform": self.transform,
            "https://bioprotocols.org/paml/primitives/culturing/Culture": self.culture,
            "https://bioprotocols.org/paml/primitives/plate_handling/Incubate": self.incubate,
            "https://bioprotocols.org/paml/primitives/plate_handling/Hold": self.hold,
            "https://bioprotocols.org/paml/primitives/liquid_handling/Dilute": self.dilute,
            "https://bioprotocols.org/paml/primitives/liquid_handling/DiluteToTargetOD": self.dilute_to_target_od,
            "http://sbols.org/unspecified_namespace/ContainerSet": self.define_containers
        }

    def on_begin(self):
        if self.execution:
            protocol = self.execution.protocol.lookup()
            self.markdown_converter = MarkdownConverter(protocol.document)
            self.markdown += self.markdown_converter.markdown_header(protocol)
            self.markdown += self._materials_markdown(protocol)
            self.markdown += self._inputs_markdown(self.execution.parameter_values)

    def _inputs_markdown(self, parameter_values):
        markdown = '\n\n## Protocol Inputs:\n'
        for i in parameter_values:
            parameter = i.parameter.lookup()
            if parameter.property_value.direction == uml.PARAMETER_IN:
                markdown += self._parameter_value_markdown(i)
        return markdown

    def _outputs_markdown(self, parameter_values):
        markdown = '\n\n## Protocol Outputs:\n'
        for i in parameter_values:
            parameter = i.parameter.lookup()
            if parameter.property_value.direction == uml.PARAMETER_OUT:
                markdown += self._parameter_value_markdown(i, True)
        return markdown

    def _materials_markdown(self, protocol):
        document_objects = protocol.document.objects
        components = [x for x in protocol.document.objects if isinstance(x, sbol3.component.Component)]
        materials = {x.name: x for x in components}
        markdown = '\n\n## Protocol Materials:\n'
        for name, material  in materials.items():
            markdown += f"* [{name}]({material.types[0]})\n"
        return markdown

    def _parameter_value_markdown(self, pv : paml.ParameterValue, is_output=False):
        parameter = pv.parameter.lookup().property_value
        value = pv.value.value.lookup() if isinstance(pv.value, uml.LiteralReference) else pv.value.value
        units = tyto.OM.get_term_by_uri(value.unit) if isinstance(value, sbol3.om_unit.Measure) else None
        value = str(f"{value.value} {units}")  if units else str(value)
        if is_output:
            return f"* `{parameter.name}`"
        else:
            return f"* `{parameter.name}` = {value}"

    def _steps_markdown(self):
        markdown = '\n\n## Steps\n'
        for i, step in enumerate(self.markdown_steps):
            markdown += str(i + 1) + '. ' + step + '\n'
        return markdown

    def on_end(self):
        self.markdown += self._outputs_markdown(self.execution.parameter_values)
        self.markdown_steps += [self.reporting_step()]
        self.markdown += self._steps_markdown()
        with open(self.out_path, "w") as f:
            f.write(self.markdown)

    def reporting_step(self):
        output_parameters = []
        for i in self.execution.parameter_values:
            parameter = i.parameter.lookup()
            value = i.value.value.lookup() if isinstance(i.value, uml.LiteralReference) else i.value.value
            if parameter.property_value.direction == uml.PARAMETER_OUT:
                #output_parameters.append(f"`{parameter.property_value.name}` from `{value}`")
                output_parameters.append(value.name)
        output_parameters = ", ".join(output_parameters)
        return f"Report values for {output_parameters}."

    def define_container(self, record: paml.ActivityNodeExecution):
        results = {}
        call = record.call.lookup()
        parameter_value_map = call.parameter_value_map()

        spec = parameter_value_map["specification"]['value']
        #samples_var = parameter_value_map["samples"]['value']

        ## Define a container

        l.debug(f"define_container:")
        l.debug(f" specification: {spec}")
        #l.debug(f" samples: {samples_var}")

        try:
            possible_container_types = self.resolve_container_spec(spec)
            containers_str = ",".join([f"\n\t[{c.split('#')[1]}]({c})" for c in possible_container_types])
            self.markdown_steps += [f"Provision a container named `{spec.name}` such as: {containers_str}."]
        except Exception as e:
            l.warning(e)
            
            # Assume that a simple container class is specified, rather
            # than container properties.  Then use tyto to get the 
            # container label
            container_class = ContainerOntology.uri + '#' + spec.queryString.split(':')[-1]
            container_str = ContainerOntology.get_term_by_uri(container_class)
            self.markdown_steps += [f'Provision a {container_str} to contain `{spec.name}`']
            #except Exception as e:
            #    l.warning(e)
            #    self.markdown_steps += [f"Provision a container named `{spec.name}` meeting specification: {spec.queryString}."]

        return results

    def define_containers(self, record: paml.ActivityNodeExecution):
        results = {}
        call = record.call.lookup()
        parameter_value_map = call.parameter_value_map()

        containers = parameter_value_map["specification"]["value"]
        sources = parameter_value_map["sources"]["value"]
        samples = parameter_value_map["samples"]["value"]
        samples.container_type = containers.get_parent().identity
        for source in sources:
            samples.contents += [source]
        assert(type(containers) is paml.ContainerSpec)

        try:
            
            # Assume that a simple container class is specified, rather
            # than container properties.  Then use tyto to get the 
            # container label
            container_class = ContainerOntology.uri + '#' + containers.queryString.split(':')[-1]
            container_str = ContainerOntology.get_term_by_uri(container_class)
            if len(sources) > 1:
                self.markdown_steps += [f'Provision {len(sources)} {container_str}s to contain `{containers.name}`']
            else:
                self.markdown_steps += [f'Provision a {container_str} to contain `{containers.name}`']
        except Exception as e:
            l.warning(e)
            self.markdown_steps += [f"Provision a container named `{spec.name}` meeting specification: {containers.queryString}."]

        return results
    # def provision_container(self, wells: WellGroup, amounts = None, volumes = None, informatics = None) -> Provision:
    def provision_container(self, record: paml.ActivityNodeExecution):
        results = {}
        call = record.call.lookup()
        parameter_value_map = call.parameter_value_map()

        destination = parameter_value_map["destination"]["value"]
        #dest_wells = self.var_to_entity[destination]
        value = parameter_value_map["amount"]["value"].value
        units = parameter_value_map["amount"]["value"].unit
        units = tyto.OM.get_term_by_uri(units)
        resource = parameter_value_map["resource"]["value"]
        #resource = self.resolutions[resource]
        l.debug(f"provision_container:")
        l.debug(f" destination: {destination}")
        l.debug(f" amount: {value} {units}")
        l.debug(f" resource: {resource}")

        resource_str = f"[{resource.name}]({resource.types[0]})"
        destination_str = f"`{destination.source.lookup().value.lookup().value.name}({destination.mask})`"
        self.markdown_steps += [f"Pipette {value} {units} of {resource_str} into {destination_str}."]


        return results

    def plate_coordinates(self, record: paml.ActivityNodeExecution):
        results = {}
        call = record.call.lookup()
        parameter_value_map = call.parameter_value_map()

        source = parameter_value_map["source"]["value"]
        #container = self.var_to_entity[source]
        coords = parameter_value_map["coordinates"]["value"]

        self.var_to_entity[parameter_value_map['samples']["value"]] = coords
        l.debug(f"plate_coordinates:")
        l.debug(f"  source: {source}")
        l.debug(f"  coordinates: {coords}")

        return results

    def measure_absorbance(self, record: paml.ActivityNodeExecution):
        results = {}
        call = record.call.lookup()
        parameter_value_map = call.parameter_value_map()

        wl = parameter_value_map["wavelength"]["value"]
        wl_units = tyto.OM.get_term_by_uri(wl.unit)
        samples = parameter_value_map["samples"]["value"]
        #wells = self.var_to_entity[samples]
        measurements = parameter_value_map["measurements"]["value"]

        l.debug(f"measure_absorbance:")
        l.debug(f"  samples: {samples}")
        l.debug(f"  wavelength: {wl.value} {wl_units}")
        l.debug(f"  measurements: {measurements}")

        # Lookup sample container to get the container name, and use that
        # as the sample label
        if isinstance(samples, paml.SampleMask):
            # SampleMasks are generated by the PlateCoordinates primitive
            # Their source does not directly reference a SampleArray directly,
            # rather through a LiteralReference and LiteralIdentified
            samples = samples.source.lookup().value.lookup().value
        samples_str = record.document.find(samples.container_type).value.name

        # Provide an informative name for the measurements output
        measurements.name = f'absorbance measurements for {samples_str}'

        self.markdown_steps += [f'Make absorbance measurements of {samples_str} at {wl.value} {wl_units}.']

    def measure_fluorescence(self, record: paml.ActivityNodeExecution):
        results = {}
        call = record.call.lookup()
        parameter_value_map = call.parameter_value_map()

        excitation = parameter_value_map['excitationWavelength']['value']
        emission = parameter_value_map['emissionWavelength']['value']
        bandpass = parameter_value_map['emissionBandpassWidth']['value']
        samples = parameter_value_map['samples']['value']
        measurements = parameter_value_map["measurements"]["value"]

        # Lookup sample container to get the container name, and use that
        # as the sample label
        if isinstance(samples, paml.SampleMask):
            # SampleMasks are generated by the PlateCoordinates primitive
            # Their source does not directly reference a SampleArray directly,
            # rather through a LiteralReference and LiteralIdentified
            samples = samples.source.lookup().value.lookup().value
        samples_str = record.document.find(samples.container_type).value.name

        # Provide an informative name for the measurements output
        measurements.name = f'fluorescence measurements for {samples_str}'

        # Add to markdown
        self.markdown_steps += [f'Make fluorescence measurements of {samples_str} with excitation wavelength of {measurement_to_text(excitation)} and emission filter of {measurement_to_text(emission)} and {measurement_to_text(bandpass)} bandpass']


    def vortex(self, record: paml.ActivityNodeExecution):
        call = record.call.lookup()
        parameter_value_map = call.parameter_value_map()
        duration = None
        if 'duration' in parameter_value_map:
            duration_measure = parameter_value_map["duration"]["value"]
            duration_scalar = duration_measure.value
            duration_units = tyto.OM.get_term_by_uri(duration_measure.unit)
        samples = parameter_value_map["samples"]["value"]

        # Add to markdown
        text = f"Vortex {samples}"
        if duration:
            text += f' for {duration_scalar} {duration_units}'
        self.markdown_steps += [text]

    def discard(self, record: paml.ActivityNodeExecution):
        call = record.call.lookup()
        parameter_value_map = call.parameter_value_map()

        samples = parameter_value_map["samples"]["value"]
        amount = parameter_value_map['amount']['value']
        amount_units = tyto.OM.get_term_by_uri(amount.unit)

        # Add to markdown
        text = f"`Discard {amount} {amount_units} of {samples.name})`"
        self.markdown_steps += [text]

    def transfer(self, record: paml.ActivityNodeExecution):
        call = record.call.lookup()
        parameter_value_map = call.parameter_value_map()

        source = parameter_value_map['source']['value']
        destination = parameter_value_map['destination']['value']
        amount_measure = parameter_value_map['amount']['value']
        amount_scalar = amount_measure.value
        amount_units = tyto.OM.get_term_by_uri(amount_measure.unit)
        if 'dispenseVelocity' in parameter_value_map:
            dispense_velocity = parameter_value_map['dispenseVelocity']['value']

        # Get destination container type
        destination_coordinates = ''
        if isinstance(destination, paml.SampleMask):
            # Currently SampleMasks are generated by the PlateCoordinates primitive
      
            # Since we are dealing with PlateCoordinates, try to get the wells
            try:
                destination_coordinates = destination.get_parent().get_parent().token_source.lookup().node.lookup().input_pin('coordinates').value.value
            except:
                pass

            # Get the corresponding SampleArray. The source property does not directly reference a SampleArray,
            # rather through a LiteralReference and LiteralIdentified
            # (this seems to bend the official specification a little)
            destination = destination.source.lookup().value.lookup().value

        container_spec = record.document.find(destination.container_type).value
        container_class = ContainerOntology.uri + '#' + container_spec.queryString.split(':')[-1]
        container_str = ContainerOntology.get_term_by_uri(container_class)

        # Add to markdown
        text = f"Transfer {amount_scalar} {amount_units} of `{source.name}` to {container_str}"
        if destination_coordinates:
            text += f' wells {destination_coordinates}'
        self.markdown_steps += [text]

    def culture(self, record: paml.ActivityNodeExecution):
        call = record.call.lookup()
        parameter_value_map = call.parameter_value_map()
        inocula = parameter_value_map['inoculum']['value']
        growth_medium = parameter_value_map['growth_medium']['value']
        volume = parameter_value_map['volume']['value']
        volume_scalar = volume.value
        volume_units = tyto.OM.get_term_by_uri(volume.unit)
        duration = parameter_value_map['duration']['value']
        duration_scalar = duration.value
        duration_units = tyto.OM.get_term_by_uri(duration.unit)
        orbital_shake_speed = parameter_value_map['orbital_shake_speed']['value']
        temperature = parameter_value_map['temperature']['value']
        temperature_scalar = temperature.value
        temperature_units = tyto.OM.get_term_by_uri(temperature.unit)
        container = parameter_value_map['container']['value']

        # Lookup sample container to get the container name, and use that
        # as the sample label
        container_str = record.document.find(container.container_type).value.name
        inocula_names = get_sample_names(inocula, error_msg='Culture execution failed. All input inoculum Components must specify a name.')

        text = f'Inoculate `{inocula_names[0]}` into {volume_scalar} {volume_units} of {growth_medium.name} in {container_str} and grow for {measurement_to_text(duration)} at {measurement_to_text(temperature)} and {orbital_shake_speed.value} rpm.'
        text += repeat_for_remaining_samples(inocula_names, repeat_msg=' Repeat this procedure for the other inocula: ')
        self.markdown_steps += [text]

    def incubate(self, record: paml.ActivityNodeExecution):
        call = record.call.lookup()
        parameter_value_map = call.parameter_value_map()

        location = parameter_value_map['location']['value']
        duration = parameter_value_map['duration']['value']
        shakingFrequency = parameter_value_map['shakingFrequency']['value']
        temperature = parameter_value_map['temperature']['value']
        text = f'Incubate {location.name} for {measurement_to_text(duration)} at {measurement_to_text(temperature)} at {shakingFrequency.value}.'
        self.markdown_steps += [text]

    def hold(self, record: paml.ActivityNodeExecution):
        call = record.call.lookup()
        parameter_value_map = call.parameter_value_map()

        location = parameter_value_map['location']['value']
        temperature = parameter_value_map['temperature']['value']
        text = f'Hold `{location.name}` at {measurement_to_text(temperature)}.'
        self.markdown_steps += [text]

    def dilute_to_target_od(self, record: paml.ActivityNodeExecution):
        call = record.call.lookup()
        parameter_value_map = call.parameter_value_map()

        source = parameter_value_map['source']['value']
        destination = parameter_value_map['destination']['value']
        diluent = parameter_value_map['diluent']['value']
        amount = parameter_value_map['amount']['value']
        target_od = parameter_value_map['target_od']['value']
        
        # Get destination container type
        container_spec = record.document.find(destination.container_type).value
        container_class = ContainerOntology.uri + '#' + container_spec.queryString.split(':')[-1]
        container_str = ContainerOntology.get_term_by_uri(container_class)
        text = f'Dilute `{source.name}` with {diluent.name} into {container_str} to a target OD of {target_od.value} and final volume of {measurement_to_text(amount)}.'
        self.markdown_steps += [text]

    def dilute(self, record: paml.ActivityNodeExecution):
        call = record.call.lookup()
        parameter_value_map = call.parameter_value_map()

        source = parameter_value_map['source']['value']
        destination = parameter_value_map['destination']['value']
        diluent = parameter_value_map['diluent']['value']
        amount = parameter_value_map['amount']['value']
        dilution_factor = parameter_value_map['dilution_factor']['value']

        # Get destination container type
        container_spec = record.document.find(destination.container_type).value
        container_class = ContainerOntology.uri + '#' + container_spec.queryString.split(':')[-1]
        container_str = ContainerOntology.get_term_by_uri(container_class)

        text = f'Dilute `{source.name}` with {diluent.name} into the {container_str} at a 1:{dilution_factor} ratio and final volume of {measurement_to_text(amount)}.'
        self.markdown_steps += [text]

    def transform(self, record: paml.ActivityNodeExecution):
        call = record.call.lookup()
        parameter_value_map = call.parameter_value_map()
        host = parameter_value_map['host']['value']
        dna = parameter_value_map['dna']['value']
        medium = parameter_value_map['selection_medium']['value']
        if 'amount' in parameter_value_map:
            amount_measure = parameter_value_map['amount']['value']
            amount_scalar = amount_measure.value
            amount_units = tyto.OM.get_term_by_uri(amount_measure.unit)

        dna_names = get_sample_names(dna, error_msg='Transform execution failed. All input DNA Components must specify a name.')

        # Instantiate Components to represent transformants and populate
        # these into the output SampleArray
        transformants = parameter_value_map['transformants']['value']
        i_transformant = 1
        for dna_name in dna_names:

            # Use a while loop to mint a unique URI for new Components
            UNIQUE_URI = False
            while not UNIQUE_URI:
                try:
                    strain = sbol3.Component(f'transformant{i_transformant}',
                                             sbol3.SBO_FUNCTIONAL_ENTITY,
                                             name=f'{host.name} + {dna_name} transformant')
                    record.document.add(strain)

                    # Populate the output SampleArray with the new strain instances
                    transformants.contents.append(strain)
                    UNIQUE_URI = True
                except:
                    i_transformant += 1
            i_transformant += 1
          
        # Add to markdown
        text = f"Transform `{dna_names[0]}` DNA into `{host.name}` and plate transformants on {medium.name}."
        text += repeat_for_remaining_samples(dna_names, repeat_msg='Repeat for the remaining transformant DNA: ')
        self.markdown_steps += [text]


def measurement_to_text(measure: sbol3.Measure):
    measurement_scalar = measure.value
    measurement_units = tyto.OM.get_term_by_uri(measure.unit)
    return f'{measurement_scalar} {measurement_units}'

def get_sample_names(inputs: Union[paml.SampleArray, sbol3.Component], error_msg) -> list[str]:
    input_names = []
    if isinstance(inputs, paml.SampleArray):
        input_names = [i.lookup().name for i in inputs.contents]
    else:
        # in case that inputs are provided directly as a list of Components
        # (strict type-checking should have already occurred upstream)
        input_names = [i.name for i in inputs]
    if not all([name is not None for name in input_names]):
        raise ValueError(error_msg)
    return input_names            

repeat_for_remaining_samples(names: list[str], repeat_msg: str):
    if len(names) == 1:
        return ''
    elif len(names) == 2:
        return f'{repeat_msg} {names[1]}'
    elif len(names) == 3
        return f'{repeat_msg} {names[1]} and {names[2]}'
    else:
        remaining = ', '.join([f'`{name}`' for name in names[1:-1]])
        remaining += f', and names[-1]'
        return f'{repeat_msg} {remaining}'

