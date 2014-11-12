import time
from scipy.spatial.distance import euclidean
from fastmc.proto import Slot
import numpy as np
from math import floor
import threading
import types
from sys import maxint


class Player(object):

    def __init__(self, proto, send_queue, world, auto_defend=True):
        self.proto = proto
        self._send_queue = send_queue
        self.x = None
        self.y = None
        self.z = None
        self.yaw = None
        self.pitch = None
        self._health = 0
        self._food = 0
        self._food_saturation = 0
        self._xp_bar = -1
        self._xp_level = -1
        self._xp_total = -1
        self._available_enchantments = {}
        self._open_window_id = 0
        self._held_slot_num = 0
        self._cursor_slot = Slot(-1, None, None, None)
        self.windows = {}
        self.world = world
        self.on_ground = True
        self.is_moving = threading.Event()
        self._move_lock = threading.Lock()
        self.move_corrected_by_server = threading.Event()
        self._action_lock = threading.Lock()
        self._auto_defend = threading.Event()
        if auto_defend:
            self._auto_defend.set()
        self.auto_defend_mob_types = types.HOSTILE_MOBS
        self._threads = {}
        self._thread_funcs = {
            'falling': self._do_falling,
            'auto_defend': self._do_auto_defend,
        }
        self._active_threads = set(self._thread_funcs.keys())
        self.start_threads()

    def start_threads(self):
        for name, func in self._thread_funcs.iteritems():
            thread = threading.Thread(target=func, name=name)
            thread.daemon = True
            thread.start()
            self._threads[name] = thread

    def _do_falling(self):
        while True:
            if self.is_moving.is_set():
                continue
            pos = self.position
            if None in pos:
                continue
            x, y, z = pos
            standing = self.world.is_solid_block(x, y - 1, z)
            if standing is None or standing:
                continue
            next_pos = self.world.get_next_highest_solid_block(x, y, z)
            if next_pos is None:
                continue
            self.on_ground = False
            x, y, z = next_pos
            self.on_ground = self.move_to(x, y + 1, z, speed=13)
            time.sleep(0.1)

    def _do_auto_defend(self):
        while True:
            self._auto_defend.wait()
            eids_in_range = [e.eid for e in self.iter_entities_in_range(
                self.auto_defend_mob_types)]
            if not eids_in_range:
                time.sleep(0.1)
                continue
            with self._action_lock:
                self.equip_any_item_from_list([
                    'Diamond Sword',
                    'Golden Sword',
                    'Iron Sword',
                    'Stone Sword',
                    'Wooden Sword',
                ])
                for eid in eids_in_range:
                    self.send(self.proto.PlayServerboundUseEntity.id,
                              target=eid,
                              type=1
                              )
            time.sleep(0.1)

    def send(self, packet_id, **kwargs):
        self._send_queue.put((packet_id, kwargs))

    def __repr__(self):
        return 'Player(x={}, y={}, z={})'.format(self.x, self.y, self.z)

    @property
    def position(self):
        return (self.x, self.y, self.z)

    def get_position(self, dx=0, dy=0, dz=0, floor=False):
        if self.x is None:
            return (None, None, None)
        position = np.add((self.x, self.y, self.z), (dx, dy, dz))
        if floor:
            return tuple([int(i) for i in np.floor(position)])
        else:
            return tuple(position)

    @property
    def held_item(self):
        inventory = self.inventory
        if inventory is not None:
            held = inventory.get_held()
        return held[self._held_slot_num]

    @property
    def inventory(self):
        return self.windows.get(0)

    def navigate_to(self, x, y, z, speed=10, space=0, limit=maxint):
        x0, y0, z0 = self.get_position(floor=True)
        x, y, z = floor(x), floor(y), floor(z)
        path = self.world.find_path(x0, y0, z0, x, y, z, limit=limit)
        if path is None or len(path) <= space:
            return False
        for x, y, z in path[:-space]:
            if not self.move_to(x, y, z, speed=speed, center=True):
                return False
        return True

    def move_to(self, x, y, z, speed=10, center=False):
        def abs_min(n, delta):
            if n < 0:
                return max(n, -delta)
            else:
                return min(n, delta)

        if center:
            x = floor(x) + 0.5
            z = floor(z) + 0.5

        dt = 0.1
        delta = speed * dt
        self.is_moving.set()
        self.move_corrected_by_server.clear()
        while euclidean((x, y, z), self.get_position()) > 0.1:
            if self.move_corrected_by_server.is_set():
                self.move_corrected_by_server.clear()
                self.is_moving.clear()
                return False
            dx = x - self.x
            dy = y - self.y
            dz = z - self.z
            self.move(abs_min(dx, delta), abs_min(dy, delta), abs_min(dz, delta))
            time.sleep(dt)
        self.is_moving.clear()
        return True

    def move(self, dx=0, dy=0, dz=0):
        with self._move_lock:
            self.x += dx
            self.y += dy
            self.z += dz
        #print 'moving rel:', (dx, dy, dz)
        #print 'moving:', self.position

    def teleport(self, x, y, z, yaw, pitch):
        self._move_lock.acquire()
        self.x = x
        self.y = y
        self.z = z
        self.yaw = yaw
        self.pitch = pitch
        self._move_lock.release()

    def iter_entities_in_range(self, types=None, reach=4):
        for entity in self.world.iter_entities(types=types):
            if euclidean((self.x, self.y, self.z),
                         (entity.x, entity.y, entity.z)) <= reach:
                yield entity

    def get_closest_entity(self, types=None, limit=None):
        closest_entity, dist = None, None
        for entity in self.world.iter_entities(types=types):
            cur_dist = euclidean(self.position, (entity.x, entity.y, entity.z))
            if closest_entity is None or (cur_dist < limit and cur_dist < dist):
                closest_entity = entity
                dist = cur_dist
        return (closest_entity, dist)

    def enable_auto_defend(self):
        self._auto_defend.set()

    def disable_auto_defend(self):
        self._auto_defend.clear()

    def set_auto_defend_mob_types(self, mob_types):
        self.auto_defend_mob_types = mob_types

    def equip_any_item_from_list(self, item_types):
        return True

    def hunt(self, mob_types=None, count=1):
        self.enable_auto_defend()
        if mob_types:
            for _type in mob_types:
                if isinstance(_type, basestring):
                    _type = types.MobTypes().get_id(_type)
                self.auto_defend_mob_types.add(_type)
        while count > 0:
            entity, dist = self.get_closest_entity(mob_types, limit=10)
            if entity is None:
                time.sleep(1)
                continue
            self.navigate_to(entity.x, entity.y, entity.z, space=3, limit=20)
            time.sleep(0.5)
            if entity.eid not in self.world.entities:
                count -= 1
