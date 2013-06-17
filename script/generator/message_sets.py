"""
This modules contains the logic to parse and validate JSON files in the OpenXC
message set format.
"""

from __future__ import print_function
from collections import defaultdict
import operator

from xml_to_json import merge_database_into_mapping
from common import warning, fatal_error, Command, merge, find_file, \
        load_json_from_search_path, VALID_BUS_ADDRESSES, CanBus


class MessageSet(object):
    def __init__(self, name):
        self.name = name
        self.buses = defaultdict(CanBus)
        self.initializers = []
        self.loopers = []
        self.commands = []
        self.extra_sources = []

    def valid_buses(self):
        for bus in sorted(self.buses.values(),
                key=operator.attrgetter('controller')):
            if bus.controller in VALID_BUS_ADDRESSES:
                yield bus

    def active_messages(self):
        for message in self.all_messages():
            if message.enabled:
                yield message

    def all_messages(self):
        for bus in self.valid_buses():
            for message in bus.sorted_messages():
                yield message

    def active_signals(self):
        for signal in self.all_signals():
            if signal.enabled:
                yield signal

    def all_signals(self):
        for message in self.all_messages():
            for signal in message.sorted_signals():
                yield signal

    def active_commands(self):
        for command in self.commands:
            if command.enabled:
                yield command

    def validate_messages(self):
        valid = True
        for signal in self.all_signals():
            valid = valid and signal.validate()
        return valid

    def validate_name(self):
        if self.name is None:
            warning("missing message set (%s)" % self.name)
            return False
        return True

    def lookup_message_index(self, message):
        for i, candidate in enumerate(self.active_messages()):
            if candidate.id == message.id:
                return i

    def lookup_bus_index(self, bus_name):
        bus = self.buses.get(bus_name, None)
        if bus and bus.controller is not None:
            for index, candidate_bus_address in enumerate(VALID_BUS_ADDRESSES):
                if candidate_bus_address == bus.controller:
                    return index
        return None

    def _message_count(self):
        return len(list(self.all_messages()))


class JsonMessageSet(MessageSet):
    @classmethod
    def parse(cls, filename, search_paths=None):
        search_paths = search_paths or []

        data = load_json_from_search_path(filename, search_paths)

        while len(data.get('parents', [])) > 0:
            for parent_filename in data.get('parents', []):
                parent_data = load_json_from_search_path(parent_filename,
                        search_paths)
                # Merge data *into* parents, so we keep any overrides
                data = merge(parent_data, data)
                data['parents'].remove(parent_filename)

        message_set = cls(data.get('name', 'generic'))
        message_set.initializers = data.get('initializers', [])
        message_set.loopers = data.get('loopers', [])
        message_set.buses = cls._parse_buses(data)
        message_set.bit_numbering_inverted = data.get(
                'bit_numbering_inverted', True)
        message_set.extra_sources = data.get('extra_sources', [])
        message_set.commands = cls._parse_commands(data)

        raw_messages = message_set._parse_mappings(data, search_paths)
        for message_id, message in data.get('messages', {}).items():
            message['id'] = message_id
            raw_messages.append(message)
        message_set._parse_messages(raw_messages)

        return message_set

    @classmethod
    def _parse_commands(cls, data):
        return [Command(**command_data) for command_data in
                data.get('commands', [])]

    @classmethod
    def _parse_buses(cls, data):
        buses = {}
        for bus_name, bus_data in data.get('buses', {}).items():
            buses[bus_name] = CanBus(name=bus_name, **bus_data)
            if buses[bus_name].speed is None:
                fatal_error("Bus %s is missing the 'speed' attribute" %
                        bus_name)
        return buses

    def _parse_mappings(self, data, search_paths):
        all_messages = []
        for mapping in data.get('mappings', []):
            if 'mapping' not in mapping:
                fatal_error("Mapping is missing the mapping file path")


            mapping_enabled = mapping.get('enabled', True)
            if not mapping_enabled:
                warning("Mapping '%s' is disabled" % mapping['mapping'])
                # TODO we could speed up code generation by just skipping the
                # mapping here, but that makes this class less useful as a
                # general parser, since you may want to see which messages are
                # disabled from code

            bus_name = mapping.get('bus', None)
            if bus_name is None:
                warning("No default bus associated with '%s' mapping" %
                        mapping['mapping'])
            elif bus_name not in self.buses:
                fatal_error("Bus '%s' (from mapping %s) is not defined" %
                        (bus_name, mapping['mapping']))

            mapping_data = load_json_from_search_path(mapping['mapping'],
                    search_paths)
            messages = mapping_data.get('messages', None)
            if messages is None:
                fatal_error("Mapping file '%s' is missing a 'messages' field"
                        % mapping['mapping'])

            bit_numbering_inverted = mapping.get('bit_numbering_inverted',
                    self.bit_numbering_inverted)
            if 'database' in mapping:
                messages = merge(merge_database_into_mapping(
                            find_file(mapping['database'], search_paths),
                            messages)['messages'],
                        messages)

            for message_id, message in messages.items():
                message['id'] = message_id
                if 'bus' not in message:
                    message['bus'] = bus_name
                if 'enabled' not in message:
                    message['enabled'] = mapping_enabled
                if 'bit_numbering_inverted' not in message:
                    message['bit_numbering_inverted'] = bit_numbering_inverted

            all_messages.extend(messages.values())
        return all_messages

    def _parse_messages(self, messages, default_bus=None):
        for message_data in messages:
            message = self.buses[message_data['bus']].get_or_create_message(
                    message_data['id'])
            message.message_set = self
            message.merge_message(message_data)
            # TODO if we update message on the previous line, do we need to
            # re-add it or is it a pointer?
            #self.buses[message.bus_name].add_message(message)