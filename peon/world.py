from scipy.spatial.distance import euclidean
from math import floor
import smpmap
import astar
from types import (MobTypes, ItemTypes, ObjectTypes)
from window import Slot


class World(smpmap.World):
    def __init__(self):
        self.columns = {}
        self.entities = {}
        self.players = {}
        self.player_data = {}
        self.objects = {}
        self.dimmension = 0

    def iter_entities(self, types=None):
        if hasattr(types, '__iter__'):
            types = [t if isinstance(t, int)
                     else MobTypes.get_id(t)
                     for t in types]
        for entity in self.entities.values():
            if types is None or entity._type in types:
                yield entity

    def iter_objects(self, types=None, items=None):
        if hasattr(types, '__iter__'):
            types = [t if isinstance(t, int)
                     else ObjectTypes.get_id(t)
                     for t in types]
        if hasattr(items, '__iter__'):
            items = [i if isinstance(i, basestring)
                     else ItemTypes.get_name(*i)
                     for i in items]
        for obj in self.objects.values():
            if types is None or obj._type in types:
                if ObjectTypes.get_name(obj._type) == 'Item Stack':
                    slot = Slot(obj.metadata.get(10, (None, None))[1])
                    if items is None or slot.name in items:
                        yield obj
                elif items is None:
                    yield obj

    def is_solid_block(self, x, y, z):
        _type = self.get_id(x, y, z)
        return False if _type is None else ItemTypes.is_solid(_type)

    def is_unbreakable_block(self, x, y, z):
        _type = self.get_id(x, y, z)
        return False if _type is None else ItemTypes.is_unbreakable(_type)

    def is_climbable_block(self, x, y, z):
        _type = self.get_id(x, y, z)
        return False if _type is None else ItemTypes.is_climbable(_type)

    def is_breathable_block(self, x, y, z):
        _type = self.get_id(x, y, z)
        return False if _type is None else ItemTypes.is_breathable(_type)

    def is_safe_non_solid_block(self, x, y, z):
        _type = self.get_id(x, y, z)
        return False if _type is None else ItemTypes.is_safe_non_solid(_type)

    def is_liquid_block(self, x, y, z):
        _type = self.get_id(x, y, z)
        return False if _type is None else ItemTypes.is_liquid(_type)

    def is_falling_block(self, x, y, z):
        _type = self.get_id(x, y, z)
        return False if _type is None else ItemTypes.is_falling(_type)

    def is_harvestable_block(self, x, y, z):
        _type = self.get_id(x, y, z)
        meta = self.get_meta(x, y, z)
        return False if _type is None else ItemTypes.is_harvestable(_type, meta)

    def get_next_highest_solid_block(self, x, y, z):
        for y in xrange(int(y), -1, -1):
            if self.is_solid_block(x, y, z):
                return (x, y, z)

    def get_player_position(self, player_name=None, eid=None, uuid=None):
        player = None
        if player_name is not None:
            for uuid, data in self.player_data.items():
                if data.get('name', '') == player_name:
                    for eid, cur_player in self.players.items():
                        if cur_player.uuid == uuid:
                            return cur_player.get_position(floor=True)
        elif eid is not None:
            player = self.players.get(eid)
        elif uuid is not None:
            for eid, cur_player in self.players.items():
                if player.uuid == uuid:
                    player = cur_player
                    break
        if player is not None:
            return player.get_position(floor=True)

    @staticmethod
    def iter_adjacent(x, y, z, center=False):
        for dx in xrange(-1, 2):
            for dy in xrange(-1, 2):
                for dz in xrange(-1, 2):
                    if not center and (dx, dy, dz) == (0, 0, 0):
                        continue
                    yield (x + dx, y + dy, z + dz)

    def iter_adjacent_2d(x, z, center=False):
        for dx in xrange(-1, 2):
            for dz in xrange(-1, 2):
                if not center and (dx, dz) == (0, 0):
                    continue
                yield (x + dx, z + dz)

    def iter_moveable_adjacent(self, x0, y0, z0):
        for x, y, z in self.iter_adjacent(x0, y0, z0):
            if self.is_moveable(x0, y0, z0, x, y, z):
                yield (x, y, z)

    def iter_diggable_adjacent(self, x0, y0, z0):
        for x, y, z in self.iter_adjacent(x0, y0, z0):
            if self.is_diggable(x0, y0, z0, x, y, z):
                yield (x, y, z)

    def iter_reachable(self, x, y, z, _range=10):
        '''iter positions that are reachable within a given range'''
        _open = [(x, y, z)]
        closed = set([])
        while _open:
            current = _open.pop(0)
            yield current
            closed.add(current)
            for neighbor in self.iter_moveable_adjacent(*current):
                if neighbor in closed or neighbor in _open:
                    continue
                distance = euclidean((x, y, z), neighbor)
                if distance <= _range:
                    _open.append(neighbor)

    def iter_block_types(self, x, y, z, block_types, _range=None):
        '''iter blocks of a certain types'''
        block_types = [i if isinstance(i, int)
                       else ItemTypes.get_block_id(i)
                       for i in block_types]
        _open = [(x, y, z)]
        closed = set([])
        while _open:
            current = _open.pop(0)
            if self.get_id(*current) in block_types:
                yield current
            closed.add(current)
            for neighbor in self.iter_adjacent(*current):
                if neighbor in closed or neighbor in _open:
                    continue
                if self.get_id(*neighbor) == 0:  # Air block
                    continue
                if _range is None or euclidean((x, y, z), neighbor) <= _range:
                    _open.append(neighbor)

    def iter_block_types_surrounding_chunks(self, x, y, z, _type):
        if isinstance(_type, basestring):
            _type = ItemTypes.get_block_id(_type) << 4  # pre bit shifted
        for (cx, cz) in self.iter_adjacent_2d(x // 16, z // 16, center=True):
            column = self.columns.get((cx, cz))
            if column is None:
                continue
            for y_index, chunk in enumerate(column.chunks):
                if chunk is None:
                    continue
                for index, data in enumerate(chunk['block_data'].data):
                    if _type == data:
                        dy, r = divmod(index, 16)
                        dz, dx = divmod(r, 16)
                        x, y, z = dx + cx * 16, dy + y_index, dz + cz * 16
                        yield (x, y, z)

    def get_name(self, x, y, z):
        return ItemTypes.get_block_name(self.get_id(x, y, z))

    def is_moveable(self, x0, y0, z0, x, y, z, with_floor=True):
        # check target spot
        if with_floor:
            if not self.is_standable(x, y, z):
                return False
        else:
            if not self.is_passable(x, y, z):
                return False

        if y > y0:
            return all([
                self.is_passable(x0, y, z0),
                self.is_moveable(x0, y, z0, x, y, z)
            ])
        elif y < y0:
            return self.is_moveable(x0, y0, z0, x, y0, z, with_floor=False)

        # check if horizontal x z movement
        if x0 == x or z0 == z:
            return True

        # check diagonal x z movement
        return all([
            self.is_passable(x0, y, z),
            self.is_passable(x, y, z0),
            any([
                self.is_safe_non_solid_block(x, y - 1, z0),
                self.is_climbable_block(x, y - 1, z0),
                ]),
            any([
                self.is_safe_non_solid_block(x0, y - 1, z),
                self.is_climbable_block(x0, y - 1, z),
                ]),
        ])

    def is_diggable(self, x0, y0, z0, x, y, z, with_floor=True):
        # if moveable, no need to dig
        if self.is_moveable(x0, y0, z0, x, y, z, with_floor=with_floor):
            return True

        # don't dig straight up or down
        if (x0, z0) == (x, z):
            return False

        # check target spot
        if not all([
            self.is_safe_to_break(x, y, z),
            self.is_safe_to_break(x, y + 1, z)
        ]):
            return False

        if with_floor and not self.is_climbable_block(x, y - 1, z):
            return False

        if y > y0:
            return all([
                self.is_safe_to_break(x0, y, z0),
                self.is_safe_to_break(x0, y + 1, z0),
                self.is_diggable(x0, y, z0, x, y, z)
            ])
        elif y < y0:
            return self.is_diggable(x0, y0, z0, x, y0, z, with_floor=False)

        # check if horizontal x z movement
        if x0 == x or z0 == z:
            return True

        # check diagonal x z movement
        return all([
            self.is_safe_to_break(x0, y, z),
            self.is_safe_to_break(x0, y + 1, z),
            self.is_safe_to_break(x, y, z0),
            self.is_safe_to_break(x, y + 1, z0),
            any([
                self.is_safe_non_solid_block(x, y - 1, z0),
                self.is_climbable_block(x, y - 1, z0),
                ]),
            any([
                self.is_safe_non_solid_block(x0, y - 1, z),
                self.is_climbable_block(x0, y - 1, z),
                ]),
        ])

    def get_blocks_to_break(self, x0, y0, z0, x, y, z):
        if (x0, y0, z0) == (x, y, z):
            return set([])
        positions = set([
            (x, y, z),
            (x, y + 1, z),
        ])
        if y > y0:
            positions.update([
                (x0, y, z0),
                (x0, y + 1, z0),
            ])
            positions.update(self.get_blocks_to_break(x0, y, z0, x, y, z))
        elif y < y0:
            positions.update(self.get_blocks_to_break(x0, y0, z0, x, y0, z))

        if x0 == x or z0 == z:
            return set([p for p in positions if self.is_solid_block(*p)])

        positions.update([
            (x0, y, z),
            (x0, y + 1, z),
            (x, y, z0),
            (x, y + 1, z0),
        ])

        return set([p for p in positions if self.is_solid_block(*p)])

    def is_safe_to_break(self, x, y, z):
        if self.is_unbreakable_block(x, y, z):
            return False
        if self.is_falling_block(x, y + 1, z):
            return False
        for x, y, z in self.iter_adjacent(x, y, z):
            if self.is_liquid_block(x, y, z):
                return False
        return True

    def is_standable(self, x, y, z):
        return all([
            self.is_breathable_block(x, y + 1, z),
            self.is_safe_non_solid_block(x, y + 1, z),
            self.is_safe_non_solid_block(x, y, z),
            self.is_climbable_block(x, y - 1, z),
        ])

    def is_passable(self, x, y, z):
        return all([
            self.is_safe_non_solid_block(x, y + 1, z),
            self.is_safe_non_solid_block(x, y, z),
        ])

    def find_path(self, x0, y0, z0, x, y, z, space=0, timeout=10,
                  digging=False, debug=None):

        def iter_moveable_adjacent(pos):
            return self.iter_moveable_adjacent(*pos)

        def iter_diggable_adjacent(pos):
            return self.iter_diggable_adjacent(*pos)

        def block_breaking_cost(p1, p2, weight=7):
            x0, y0, z0 = p1
            x, y, z = p2
            return 1 + len(self.get_blocks_to_break(x0, y0, z0, x, y, z)) * 0.5

        # TODO pre-check the destination for a spot to stand
        if digging:
            if not all([
                self.is_safe_to_break(x, y, z),
                self.is_safe_to_break(x, y + 1, z),
            ]):
                return []
            neighbor_function = iter_diggable_adjacent
            cost_function = block_breaking_cost
        else:
            if not self.is_standable(x, y, z):
                return []
            neighbor_function = iter_moveable_adjacent
            cost_function = lambda p1, p2: euclidean(p1, p2)

        return astar.astar(
            (floor(x0), floor(y0), floor(z0)),              # start_pos
            neighbor_function,                              # neighbors
            lambda p: euclidean(p, (x, y, z)) <= space,     # at_goal
            0,                                              # start_g
            cost_function,                                  # cost
            lambda p: euclidean(p, (x, y, z)),              # heuristic
            timeout,                                        # timeout
            debug                                           # debug
        )


'''
class Block(object):
    def __init__(self, x, y, z, _type, meta):
        self.x = int(floor(x))
        self.y = int(floor(y))
        self.z = int(floor(z))
        self._type = _type
        self.meta = meta

    def __repr__(self, x, y, z):
        return "Block(x={}, y={}, z={}, _type='{}', meta='{}')".format(
            self.x, self.y, self.z, self._type, str(self.meta))

    @property
    def position(self):
        return (self.x, self.y, self.z)

    @property
    def name(self):
        return ItemTypes.get_block_name(self._id)
        self.get_id(self.x, self.y, self.z)

    @property
    def _id(self):
        self.get_id(self.x, self.y, self.z)
'''
