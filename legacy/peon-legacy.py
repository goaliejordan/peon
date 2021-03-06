#!/usr/bin/python

import mc
import astar
import json
import re
from scipy.spatial.distance import cityblock, euclidean
import pickle
import csv
import collections
import functools
import math
import Queue
import socket
import struct
import sys
import threading
import time
import random
import itertools
import logging
import optparse
import atexit
import os

class MoveException(Exception):
  pass

class MineCraftBot(mc.MineCraftProtocol):
  def __init__(self, host, port, username, password=None, fighting=True):
    super(MineCraftBot, self).__init__()
    self._host = host
    self._port = port
    self._username = username
    self._password = password
    self._serverId = None
    self._status = 'idle'
    self._health = 0
    self._food = 0
    self._food_saturation = 0
    self._xp_bar = -1
    self._xp_level = -1
    self._xp_total = -1
    self._available_enchantments = {}
    self._open_window_id = 0
    self._held_slot_num = 0
    self.world = mc.World()
    self.windows = {}
    self._pos = mc.Position(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1)
    self._entityId = None
    self._levelType = None
    self._serverMode = None
    self._dimension = None
    self._difficulty = None
    self._maxPlayers = None
    self._cursor_slot = mc.Slot(itemId=-1, count=None, meta=None, data=None) 
    self._action_id = itertools.count(1)
    self._confirmations = {}
    self.bot_file = os.path.join('/tmp', self._username)
    self._threadFuncs.extend([
        #self._DoCrashThread,
        #self._DoWatchdogThread,
        self._DoPositionUpdateThread,
        ])
    if fighting:
      self._threadFuncs.append(self._DoFightingThread)
    self._handlers = {
        '\x00': self.OnKeepAlive,
        '\x01': self.OnLogin,
        '\x02': self.OnHandshake,
        '\x03': self.OnChatMessage,
        '\x05': self.OnEntityEquipment,
        '\x08': self.OnUpdateHealth,
        '\x0d': self.OnPlayerPositionLook,
        '\x14': self.OnSpawnNamedEntity,
        '\x18': self.OnSpawnMob,
        '\x1d': self.OnDestroyEntity,
        '\x1f': self.OnEntityRelativeMove,
        '\x21': self.OnEntityLookRelativeMove,
        '\x22': self.OnEntityTeleport,
        '\x2b': self.OnSetExperience,
        '\x33': self.world.MapChunk,
        '\x34': self.OnMultiBlockChange,
        '\x35': self.OnBlockChange,
        '\x64': self.OnOpenWindow,
        '\x67': self.OnSetSlot,
        '\x68': self.OnSetWindowItems,
        '\x69': self.OnUpdateWindowProperty,
        '\x6a': self.OnConfirmTransaction,
        }
    self._block_names, self._block_ids = self.get_blocktypes()
    if os.path.isfile(self.bot_file):
      raise Exception("%s is already logged in" % self._username)
    open(self.bot_file, 'w').close()
    atexit.register(self.delbotfile)
    if password is None:
      self.Login()
    else:
      self.Login(authenticate=True)
    #self.FloatDown()

  def get_blocktypes(self, filename='blocktypes.csv'):
    c = [ l for l in csv.DictReader(open(filename), skipinitialspace=True) ]
    block_names = dict([(int(l['dec']), l['type']) for l in c ])
    block_ids = dict([(l['type'], int(l['dec'])) for l in  c ])
    return block_names, block_ids

  def delbotfile(self):
    os.remove(self.bot_file)

  def Login(self, authenticate=False):
    self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    self._sock.connect((self._host, self._port))
    self._sockGeneration += 1
    self.StartThreads()
    if authenticate:
      self._sessionId = self.GetSessionId(self._username, self._password)
      logging.info('sessionId: %d', self._sessionId)
      self.SendHandshake(self._username, self._host, self._port)
      self.WaitFor(lambda: self._serverId is not None)
      logging.info('serverId: %d', self._serverId)
      logging.info('joinserver status: %s', str(self.JoinServer(self._username, self._sessionId, self._serverId)))
    logging.info('sending login. server: %s username: %s', self._host, self._username)
    self.SendLogin(self._username)

  def _DoCrashThread(self):
    time.sleep(60)
    while self._sock is not None:
      self._buf = '\x1bADIDEA'
      time.sleep(1)

  def _DoWatchdogThread(self):
    try:
      myGeneration = self._sockGeneration
      time.sleep(5)
      while all(t.is_alive() for t in self._threads):
        time.sleep(1)
      deadTime = time.time()
      self._sock = None
      self._sendQueue.put(None)
      self._sendQueue = None
      def OtherThreadIsAlive():
        return len([t for t in self._threads if t.is_alive()]) > 1
      while OtherThreadIsAlive() and time.time() - deadTime < 5:
        time.sleep(1)
      if OtherThreadIsAlive():
        time.sleep(3)
      self._buf = ''
      self._sendQueue = Queue.Queue(10)
      self._pos = mc.Position(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1)
      self.world = mc.World()
      self.windows = {}
      self.Login()
      self.FloatDown()
    finally:
      logging.error("Watchdog thread exiting, %s", str(myGeneration))

  def _DoPositionUpdateThread(self):
    try:
      self.WaitFor(lambda: self._pos.x != 0.0 and self._pos.y != 0.0)
      myGeneration = self._sockGeneration
      while myGeneration == self._sockGeneration:
        time.sleep(0.010)
        self.SendPositionLook()
        if os.getppid() != self.parentPID:
          logging.erro("Position update thread exiting, %s", srt(myGeneration))
    finally:
      os.kill(self.parentPID, 0)

  def _DoFightingThread(self):
    HOSTILE_MOBS = set([50, 51, 52, 53, 54, 55, 56, 58, 59, 61, 62, 63])
    try:
      self.WaitFor(lambda: self._pos.x != 0.0 and self._pos.y != 0.0)
      while True:
        time.sleep(0.10)
        if os.getppid() != self.parentPID:
          logging.erro("Fighting thread exiting, %s", srt(myGeneration))
        for eid, entity in self.world._entities.items():
          if entity._type in HOSTILE_MOBS:
            if euclidean(entity._pos.xzy(), self._pos.xzy()) <= 4:
              self.attack_entity(eid)
    finally:
      os.kill(self.parentPID, 0)

  def OnKeepAlive(self, token):
    self.Send(
        '\x00' +
        struct.pack('!i', token)
        )

  def SendPositionLook(self, pos=None):
    if pos is None:
      pos = self._pos
    self.SendPlayerPositionAndLook(pos.x, pos.y, pos.stance, pos.z, pos.yaw, pos.pitch, pos.on_ground) 

  def OnLogin(self, entityId, levelType, serverMode, dimension, difficulty, maxPlayers):
      self._entityId = entityId
      self._levelType = levelType
      self._serverMode = serverMode
      self._dimension = dimension
      self._difficulty = difficulty
      self._maxPlayers = maxPlayers

  def OnHandshake(self, serverId):
      self._serverId = serverId

  def OnChatMessage(self, chat):
    logging.info("Chat: %s", chat)
    m = re.match('<\w+> peon, (.*)', chat)
    if m is not None:
        self.run_cmd(m.group(1))

  def OnEntityEquipment(self, entity_id, slot_num, item_id, damage):
    return

  def OnUpdateHealth(self, health, food, food_saturation):
    self._health = health
    self._food= food
    self._food_saturation = food_saturation
    if health <= 0:
      self.WaitFor(lambda: self._dimension is not None)
      self.SendRespawn(self._dimension, self._difficulty, self._levelType)

  def OnPlayerPositionLook(self, x, stance, y, z, yaw, pitch, onGround):
    pos = mc.Position(x, y, stance, z, yaw, pitch, onGround)
    self.SendPositionLook(pos)
    self._pos = mc.Position(x, y, stance, z, yaw, pitch, 1)

  def OnSpawnNamedEntity(self, eid, player_name, x, y, z, yaw, pitch, current_item):
    self.world._entities[eid] = mc.Entity(
      eid, 0, x, y, z, yaw, pitch, player_name=player_name, current_item=current_item)

  def OnSpawnMob(self, eid, etype, x, y, z, yaw, pitch, head_yaw, metadata):
    self.world._entities[eid] = mc.Entity(
      eid, etype, x, y, z, yaw, pitch, head_yaw=head_yaw, metadata=metadata)

  def OnDestroyEntity(self, eid):
    if eid in self.world._entities:
      del self.world._entities[eid]

  def OnEntityRelativeMove(self, eid, x, y, z):
    if eid in self.world._entities:
      self.world._entities[eid].Move(x, y, z)

  def OnEntityLookRelativeMove(self, eid, x, y, z, yaw, pitch):
    if eid in self.world._entities:
      self.world._entities[eid].Move(x, y, z)

  def OnEntityTeleport(self, eid, x, y, z, yaw, pitch):
    if eid in self.world._entities:
      self.world._entities[eid].Teleport(x, y, z)

  def OnEntityHeadLook(self, eid, head_yaw):
    if eid in self.world._entities:
      self.world._entities[eid]._head_yaw = head_yaw

  def OnEntityStatus(self, eid, status):
    if eid in self.world._entities and status == 3:
      del self.world._entities[eid]

  def OnSetExperience(self, bar, level, total):
    self._xp_bar = bar
    self._xp_level = level
    self._xp_total = total

  def OnMultiBlockChange(self, blocks):
    for x, z, y, newType, newMeta in blocks:
      self.world.SetBlock(x, z, y, newType, newMeta)

  def OnBlockChange(self, x, y, z, newType, newMeta):
    self.world.SetBlock(x, z, y, newType, newMeta)

  def OnOpenWindow(self, window_id, inventory_type, window_title, num_slots):
    self._open_window_id = window_id
    if window_id not in self.windows:
      time.sleep(1)
    if window_id in self.windows:
      self.windows[window_id].inventory_type = inventory_type
      self.windows[window_id].window_title = window_title

  def OnCloseWindow(self, window_id):
    self._open_window_id = 0

  def OnMapChunks(self, chunk):
    self._chunks[chunk.chunkX, chunk.chunkZ] = chunk

  def OnSetSlot(self, windowId, slotIndex, slot):
    if windowId == -1 and slotIndex == -1:
      self._cursor_slot = slot
    elif windowId in self.windows:
      self.windows[windowId].SetSlot(slotIndex, slot)

  def OnSetWindowItems(self, windowId, slots):
    window = mc.Window(windowId, slots)
    self.windows[windowId] = window

  def OnUpdateWindowProperty(self, window_id, window_property, value):
    self._available_enchantments[window_property] = value

  def OnConfirmTransaction(self, window_id, action_id, accepted):
    self._confirmations[action_id] = mc.Confirmation(window_id, action_id, accepted)
    if not accepted:
        self.SendConfirmTransaction(window_id, action_id, accepted)

  def break_block(self, x, z, y, face=1, retries=3, auto_move=True):
    xzy = mc.Xzy(x, z, y)
    if auto_move:
      m= self.iter_nearest_moveable(xzy)
      block = m.next()
      while euclidean(xzy, block) <= 6:
        if self.nav_to(*block): break
        block = m.next()
      else:
        logging.error('too far to break block')
        return False
    blocktype = self.world.GetBlock(*xzy)
    if blocktype is not None:
      self.get_best_tool(blocktype)
    for i in range(retries):
      logging.debug('sending dig command for: %s', str(xzy))
      self.SendPlayerDigging(0, x, y, z, face)
      time.sleep(0.1)
      self.SendPlayerDigging(2, x, y, z, face)
      if self.WaitFor(lambda: self.world.GetBlock(x, z, y) == 0, timeout=5):
        return True
    else:
      logging.error('could not break block: %s, type: %d', str(xzy), blocktype)
      return False

  def nav_to(self, x, z, y): 
    if not self.WaitFor(lambda: self._pos.x != 0.0 and self._pos.y != 0.0):
      logging.error('current position could not be found')
      return False
    botXzy = self._pos.xzy()
    nextXzy = mc.Xzy(x, z, y)
    if botXzy == nextXzy:
      return True
    distance = cityblock(botXzy, nextXzy)
    path = self.find_path(nextXzy, limit=min(sys.maxint, distance**2))
    if path is None:
      logging.error('could not find path')
      return False
    for step in path:
      if not self.MoveTo(*step):
        logging.error('could not make it to: %s failed at: %s', str(nextXzy), str(step))
        return False
    return True

  def MoveTo(self, x, z, y, speed=4.25, onGround=True):
    x+=0.5
    z+=0.5
    def MyDist(x, z, y):
      return abs(pos.x - x) + abs(pos.z - z) + abs(pos.y - y)
    def Go(x=None, z=None, y=None):
      logging.debug('moving to: (%d, %d, %d)', x, z, y)
      self._pos = mc.Position(x, y, y+1, z, yaw, 0, onGround)
    pos = self._pos
    yaw = pos.yaw
    if z - pos.z > .9:
      yaw = 0
    if z - pos.z < - .9:
      yaw = 180
    if x - pos.x > .9:
      yaw = 270
    if x - pos.x < - .9:
      yaw = 90
    tau = 0.010
    delta = speed * tau
    while MyDist(x, z, y) > (delta * 2):
      if pos.x - x > 0:
        new_x = pos.x - delta
      else:
        new_x = pos.x + delta
      if pos.z - z > 0:
        new_z = pos.z - delta
      else:
        new_z = pos.z + delta
      if pos.y - y > 0:
        new_y = pos.y - delta
      else:
        new_y = pos.y + delta
      Go(new_x, new_z, new_y)
      time.sleep(tau)
      if (self._pos.x, self._pos.z, self._pos.y) != (new_x, new_z, new_y):
        logging.error('did not move: %s', str(self._pos.xzy()))
        return False
      pos = self._pos
    Go(x, z, y)
    time.sleep(tau)
    if (self._pos.x, self._pos.z, self._pos.y) != (x, z, y):
      logging.error('did not move: %s', str(self._pos.xzy()))
      return False
    return True

  def FloatDown(self):
    logging.debug('floating down')
    logging.debug('waiting for pos to load')
    self.WaitFor(lambda: self._pos.x != 0.0 and self._pos.y != 0.0)
    logging.debug('waiting for block to load')
    self.WaitFor(lambda: self.world.GetBlock(
      self._pos.x, self._pos.z, self._pos.y) is not None)
    pos = self._pos.xzy()
    logging.debug('waiting for block to load')
    self.MoveTo(*pos)
    for y in range(pos.y + 1, 0, -1):
      pos = pos._replace(y=y)
      logging.debug('floating down: %s', str(pos))
      if self.world.IsStandable(*pos):
        logging.info('floating down: %s', str(pos))
        self.MoveTo(*pos)
        return

  def attack_entity(self, eid, times=5, delay=0.01):
    for i in xrange(times):
      self.SendUseEntity(self._entityId, eid, 1)
      time.sleep(delay)

  def get_best_tool(self, blockType):
    best_tools = (
        (set([1,4,14,15,16,48,61,87,89,98,109,112,113,114]), set([257, 278])), # pickaxe
        (set([2,3,12,13,88]), set([256, 277])), # shovel
        (set([5,17,18,47,53,54,58,64,72,106,107,125,126]), set([258, 279])), # axe
      )
    for blocktypes, tool_ids in best_tools:
      if blockType in blocktypes:
        for tool_id in tool_ids:
          if self.equip_tool(tool_id):
            return True
        return False
    return False

  def change_held_slot(self, slot_num):
    self.SendHeldItemChange(slot_num)
    self._held_slot_num = slot_num

  def get_slot(self, window_id, slot_num):
    return self.windows[window_id]._slots[slot_num]

  def click_slot(self, window_id, slot_num, right_click=0, shift=0):
    action_id = self._action_id.next()
    if slot_num in range(len(self.windows[window_id]._slots)):
      slot_data = self.get_slot(window_id, slot_num)
    else:
      slot_data = mc.Slot(itemId=-1, count=None, meta=None, data=None) 
    self.SendClickWindow(window_id, slot_num, right_click, action_id, shift, slot_data)
    if self.WaitFor(lambda: action_id in self._confirmations.keys(), timeout=5):
      if self._confirmations[action_id].accepted:
        if shift == 0:
          if slot_num in range(len(self.windows[window_id]._slots)):
            self.windows[window_id]._slots[slot_num] = self._cursor_slot
          self._cursor_slot = slot_data
        return True
    return False

  def find_tool(self, tool_id, window_id=0, storage_only=False, inventory_only=False, held_only=False, no_data=False):
    if storage_only:
      start = 0
      end = len(self.windows[window_id]._slots) - 35
    elif inventory_only:
      start = len(self.windows[window_id]._slots) - 36
      end = len(self.windows[window_id]._slots)
    elif held_only:
      start = len(self.windows[window_id]._slots) - 9
      end = len(self.windows[window_id]._slots)
    else:
      start = 0
      end = len(self.windows[window_id]._slots)

    for i, slot in enumerate(self.windows[window_id]._slots[start:end], start):
      if slot.itemId == tool_id and (not no_data or slot.data is None):
        return i
      elif tool_id is None and slot.itemId != -1 and (not no_data or slot.data is None):
        return i
    return None

  def equip_tool(self, tool_id):
    if self.get_slot(0, self._held_slot_num+36).itemId == tool_id: return True
    slot_num = self.find_tool(tool_id, held_only=True)
    if slot_num is not None:
      self.change_held_slot(slot_num-36)
      return True
    slot_num = self.find_tool(tool_id, inventory_only=True)
    if slot_num is None: 
      logging.debug('could not find tool_id: %d', tool_id)
      return False
    open_slot_num = self.find_tool(-1, held_only=True)
    if open_slot_num is None:
      held_slot_num = random.randrange(36,45)
      if not self.click_slot(0, held_slot_num, shift=True):
        logging.debug('could not move held item to inventory: %d', held_slot_num)
        return False
    if not self.click_slot(0, slot_num): 
      logging.debug('could not click on slot num: %d', slot_num)
      return False
    if not self.click_slot(0, open_slot_num):
      logging.debug('could not click on slot num: %d', open_slot_num)
      return False

  def run_cmd(self, cmd):
        args = cmd.split()
        if len(args) == 0:
            return
        elif cmd == 'where are you?':
            self.SendChat('x: %d, y: %d, z: %d' % (self._pos.x, self._pos.y, self._pos.z))

  def dig_area(self, bbox, home=None, dump=False, dig_height=0, ignore_blocktypes = [0]):
    logging.info('going to dig: %s', str(bbox))
    time.sleep(3)
    best_against = {
      'pick': [1,4,14,15,16],
      'shovel': [2,3,12,13]
      }
    last_block_type = -1 
    y_range = range(max(bbox['y']), min(bbox['y']), -1)
    z_range = range(min(bbox['z']), max(bbox['z']))
    random.shuffle(z_range)
    x_range = range(min(bbox['x']), max(bbox['x']))
    for y in y_range:
        for z in z_range:
           for x in x_range:
                blockXzy = mc.Xzy(x, z, y)
                if self.world.GetBlock(*blockXzy) is None:
                    logging.info("Waiting for chunks to load...")
                    self.nav_to(x, z, max(bbox['y']))
                    self.WaitFor(lambda: self.world.GetBlock(*blockXzy) is not None)
                blockType = self.world.GetBlock(*blockXzy)
                if blockType in ignore_blocktypes:
                    continue
                if last_block_type != blockType:
                    last_block_type = blockType
                    for tool_name, block_list in best_against.iteritems():
                        if blockType in block_list:
                            if not self.get_best_tool(blockType, tool_name) and home is not None:
                              logging.info('going home to get better tools: %s', str(home))
                              self.nav_to(*home)
                              while not self.get_best_tool(blockType, tool_name):
                                  self.remove_non_tools()
                                  self.move_tools_to_held()
                                  time.sleep(10)
                              self.nav_to(x, z, y + dig_height)
                self.nav_to(x, z, y + dig_height)
                if self.break_block(x, z, y):
                  sys.stdout.write('.')
                else:
                  sys.stdout.write('!')
                sys.stdout.flush()

  def dig_to(self, x, z, y):
    self.MoveTo(*self._pos.xzy())
    path = self.find_path(mc.Xzy(x,z,y), reachable_test=self.world.IsDiggable)
    if path is None:
      logging.error('could not find path')
      return False
    logging.debug('path: %s', str(path))
    for p in path:
      logging.debug('dig: %s, type: %d', str(p), self.world.GetBlock(*p))
      if self.break_block(*p, auto_move=False) and self.break_block(p.x, p.z, p.y + 1, auto_move=False):
        if not self.MoveTo(*p):
          logging.error('could not move to: %s made it to: %s', str(p), str(self._pos.xzy()))
          return False
      else:
        logging.error('could not reach: %s made it to: %s', str((x,z,y)), str(self._pos.xzy()))
        return False
    #logging.info('done')
    return True

  def find_path(self, end, reachable_test=None, limit=None):
    if limit is None:
      limit = cityblock(self._pos.xzy(), end)**2
    def iter_moveable_adjacent(start):
      l = []
      for xzy, block_type in self.world.IterAdjacent(*start):
        if reachable_test(*xzy):
          l.append(xzy)
      return l
    def at_goal(xzy):
      if xzy == end:
        return True
      else:
        return False
    def distance(a, b):
      return cityblock(a, b)
    def distance_to_goal(a):
      return cityblock(a, end)
    if reachable_test is None:
      reachable_test = self.world.IsMoveable
    pos = self._pos.xzy()
    logging.debug('looking for path from: %s to: %s', str(pos), str(end))
    path = astar.astar(
        pos, iter_moveable_adjacent, at_goal, 0, 
        distance, distance_to_goal, limit=limit)
    logging.debug('path: %s', str(path))
    return path

  def get_adjacent_blocks(self, block, min_height=0, max_height=255):
    blocks = []
    for offset_y in range(-1, 2):
      for offset_x in range(-1, 2):
        for offset_z in range(-1, 2):
          x = block.x + offset_x
          z = block.z + offset_z
          y = block.y + offset_y
          if y > 0 and y <= max_height and y >= min_height:
            blocks.append(mc.Xzy(x, z, y))
    for xzy in blocks:
      yield xzy

  def iter_find_blocktype(self, types, start=None):
    if start is None:
      start = self._pos
    start_x, start_z = int(start.x / 16), int(start.z / 16)
    s=self._iter_spiral()
    offset_x, offset_z = s.next()
    cx, cz = start_x + offset_x, start_z + offset_z
    while (cx, cz) in self.world._chunks:
      logging.info('chunk: %s', str((cx, cz)))
      for i, b in enumerate(self.world._chunks[(cx, cz)]._blocks): 
        if b in types:
          logging.info('blocktype: %d', b)
          y, r = divmod(i, 256)
          z, x = divmod(r, 16)
          yield mc.Xzy(x + cx*16, z + cz*16, y)
      offset_x, offset_z = s.next()
      cx, cz = start_x + offset_x, start_z + offset_z

  def _iter_spiral(self):
    x = 0
    y = 0
    dx = 0
    dy = -1
    while True:
      yield (x, y)
      if x == y or (x < 0 and x == -y) or (x > 0 and x == 1-y):
        dx, dy = -dy, dx
      x, y = x+dx, y+dy

  def iter_find_nearest_blocktype(self, start, types=[15], max_height=96, min_height=0):
    height_dict = {
      14: 32, #gold
      15: 64, #iron
      56: 17, #diamonds
    }
    if max_height is None:
      if set(types).issubset(height_dict.keys()):
        max_height = max([h for t, h in height_dict.items() if t in types])
      else:
        max_height = 96
    checked_blocks = set([])
    unchecked_blocks = collections.deque([start])
    block_type = 0
    while len(unchecked_blocks) != 0:
      block = unchecked_blocks.popleft()
      checked_blocks.add(block)
      for block in self.get_adjacent_blocks(block, min_height=min_height, max_height=max_height):
        if block not in checked_blocks and block not in unchecked_blocks and self.world.GetBlock(*block) is not None:
          unchecked_blocks.append(block)
      block_type = self.world.GetBlock(*block)
      if block_type in types:
        yield block

  def help_find_blocks(self, types=[14, 56], chat=True, start=None):
    LAVA = set([10,11])
    if start is None:
      start = self._pos.xzy()
    else:
      self.nav_to(start.x, start.z, 200)
    logging.info('waiting for world to load...')
    self.WaitFor(lambda: self.world.GetBlock(self._pos.x, self._pos.z, self._pos.y) is not None)
    block_iter = self.iter_find_blocktype(types, start=start)
    while True:
      block = block_iter.next()
      if block.y <= 5: continue
      if LAVA.issubset([blocktype for xzy, blocktype in bot.world.IterAdjacent(*block)]): continue
      blocktype = self.world.GetBlock(*block)
      logging.info('%s, %s', str(block), str(self._block_names[blocktype]))
      if chat:
        self.SendChat('%s. type: %s' % (str(block), self._block_names[blocktype]))
      try:
        while not self.WaitFor(lambda: self.world.GetBlock(*block) not in types):
          pass
      except KeyboardInterrupt:
        try:
          print 'break again to stop'
          time.sleep(1)
          continue
        except KeyboardInterrupt:
          return

  def get_player_position(self, player_name):
    for entity in self.world._entities.values():
      if entity._player_name == player_name:
        return entity._pos.xzy()

  def move_to_player(self, player_name):
    xzy = self.get_player_position(player_name)
    logging.info('moving to player: %s at: %s', player_name, str(xzy))
    if xzy is not None:
      self.nav_to(*xzy)

  def click_inventory_block(self, xzy):
    if self._open_window_id != 0:
      return False
    s = mc.Slot(itemId=-1, count=None, meta=None, data=None)
    self.SendPlayerBlockPlacement(xzy.x, xzy.y, xzy.z, 1,  s)
    if self.WaitFor(lambda: self._open_window_id != 0):
      if self.WaitFor(lambda: self._open_window_id in self.windows):
        return True
    return False

  def iter_nearest(self, start):
    checked_blocks = set([])
    unchecked_blocks = collections.deque([start])
    while len(unchecked_blocks) != 0:
      block = unchecked_blocks.popleft()
      checked_blocks.add(block)
      for block in self.get_adjacent_blocks(block):
        if block not in checked_blocks and block not in unchecked_blocks and self.world.GetBlock(*block) is not None:
          unchecked_blocks.append(block)
        yield block

  def iter_nearest_moveable(self, start):
    for block in self.iter_nearest(start):
      if self.world.IsMoveable(*block):
        yield block

  def iter_nearest_radius(self, start, radius):
    block = self.iter_nearest(start)
    while euclidean(start, block.next()) <= radius:
      yield block.next()

  def place_block(self, xzy):
    def get_block_direction(a, b):
      if a.y < b.y: return 0
      elif a.y > b.y: return 1
      elif a.z < b.z: return 2
      elif a.z > b.z: return 3
      elif a.x < b.x: return 4
      elif a.x > b.x: return 5
    SOLID = set(range(1, 5) + [7] + range(12, 27))
    m= self.iter_nearest_moveable(xzy)
    block = m.next()
    while euclidean(xzy, block) <= 6:
      if self.nav_to(*block): break
      block = m.next()
    else:
      logging.error('too far to place block')
      return False
    slot = self.get_slot(0, self._held_slot_num+36)
    for block, blocktype in self.world.IterAdjacent(*xzy):
      if blocktype in SOLID:
        direction = get_block_direction(xzy, block)
        logging.debug('sending block placement command: %s, direction: %d, slot_data: %s', str(block), direction, str(slot))
        self.SendPlayerBlockPlacement(block.x, block.y, block.z, direction,  slot)
        if self.WaitFor(lambda: self.world.GetBlock(*xzy) == slot.itemId, timeout=5):
          return True
        else:
          logging.error('placed blocktype did not change')
          return False
    else:
      logging.error('could not find solid adjacent block')
      return False

  def fill_adjacent_liquid(self, xzy):
    LIQUID = set(range(8,12))
    SOLID = set([1,3,4])
    for block, blocktype in self.world.IterAdjacent(*xzy):
      if blocktype in LIQUID:
        solid_blocktype = SOLID.intersection(self.get_inventory_ids())
        if len(solid_blocktype) == 0: return False
        if not self.equip_tool(solid_blocktype): return False
        if not self.place_block(block): return False
        return True

  def close_window(self):
    window_id = self._open_window_id
    self._open_window_id = 0
    self.SendCloseWindow(window_id)
    if window_id != 0:
      del self.windows[window_id]
    time.sleep(1)

  def enchant(self, tool_id, max_distance=100):
    ENCHANTMENT_TABLE=116
    pos = self._pos.xzy()
    logging.info('finding nearest enchanting table')
    table = self.iter_find_nearest_blocktype(pos, types=[ENCHANTMENT_TABLE]).next()
    if cityblock(pos, table) > max_distance:
      logging.error('too far from enchanting table') 
      return False
    logging.info('moving to enchanting table')
    if not self.nav_to(*table):
      logging.error('did not make it to enchanting table')
      return False
    self.close_window()
    logging.info('opening enchantment window')
    if not self.click_inventory_block(table):
      logging.error('could not open enchantment window')
      return False
    window_id = self._open_window_id
    logging.info('looking for tool')
    slot_num = self.find_tool(tool_id, window_id=window_id, inventory_only=True, no_data=True)
    if slot_num is None:
      logging.error('could not find tool to enchant')
      return False
    if not self.click_slot(window_id, slot_num):
      logging.error('could not grab tool to enchant')
      return False
    logging.info('looking for best enchantment level')
    target_level = min(self._xp_level, 50)
    while target_level not in self._available_enchantments.values():
      if not self.click_slot(window_id, 0):
        logging.error('failed to place tool on enchanting table')
        return False
      if self._cursor_slot.itemId == -1:
        self.WaitFor(lambda: sum(self._available_enchantments.values()) != 0, timeout=5)
        logging.debug('enchantment level: %d', max(self._available_enchantments.values()))
    enchantment_num = [ k for k, v in self._available_enchantments.items() if v == target_level ][0]
    logging.info('enchanting item')
    self.SendEnchantItem(self._open_window_id, enchantment_num)
    if not self.WaitFor(lambda: self.windows[window_id]._slots[0].data is not None, timeout=5):
      logging.error('enchant tool command failed')
      return False
    if not self.click_slot(window_id, 0):
      logging.error('could not grab enchanted tool')
      return False
    if not self.click_slot(window_id, slot_num):
      logging.error('could not place enchanted tool in inventory')
      return False
    logging.info('closing enchantment window')
    self.close_window()
    return True

  def eat(self, target_food_level=20):
    logging.info('eating')
    BREAD = 297
    if not self.equip_tool(BREAD):
      logging.debug('could not equip bread')
      return False
    slot = self.get_slot(0, self._held_slot_num+36)
    if slot is None:
      return False
    while slot.itemId == BREAD and slot.count > 0 and self._food < target_food_level:
      self.SendPlayerBlockPlacement(-1, -1, -1, -1, slot)
      time.sleep(1)
      slot = self.get_slot(0, self._held_slot_num+36)
    time.sleep(1)
    self.SendPlayerDigging(5, 0, 0, 0, 255)
    logging.debug('food level: %d, bread slot: %s', self._food, str(slot))
    if self._food == target_food_level:
      return True
    else:
      return False

  def get_inventory(self):
    return [ (slot_num, item) for slot_num, item in enumerate(self.windows[0]._slots) if item.itemId != -1 ]

  def get_inventory_ids(self):
    return [ item.itemId for slot_num, item in enumerate(self.windows[0]._slots) if item.itemId != -1 ]

  def drop_items(self, item_ids, invert=False, single=False):
    if self._open_window_id != 0:
      return False
    if len(item_ids) == 0:
      drop_list = [ slot_num for slot_num, item in self.get_inventory() ]
    elif invert:
      drop_list = [ slot_num for slot_num, item in self.get_inventory() if item.itemId not in item_ids ]
    else:
      drop_list = [ slot_num for slot_num, item in self.get_inventory() if item.itemId in item_ids ]
    for slot_num in drop_list:
      if slot_num >= 9:
        if not self.click_slot(0, slot_num):
          return False
        if not self.click_slot(0, -999):
          return False
      if single:
        return True
    else:
      return True

  def find_chest_with_item(self, tool_id, timeout=60):
    start_time = time.time()
    self.close_window()
    for chest_block in self.iter_find_blocktype([self._block_ids['chest']]):
      self.nav_to(*chest_block)
      if self.click_inventory_block(chest_block):
        if tool_id in [ item.itemId for item in self.windows[self._open_window_id]._slots ]:
          self.close_window()
          return chest_block
      self.close_window()
    if time.time() > start_time + timeout:
      logging.error('timed out looking for tool')
      self.close_window()
      return None

  def get_item_from_chest(self, tool_id, chest_block, ignore_special=False):
    if not self.click_inventory_block(chest_block): return False
    source_slot_num = self.find_tool(tool_id, window_id=self._open_window_id, storage_only=True, no_data=ignore_special)
    source_tool_id = self.windows[self._open_window_id]._slots[source_slot_num].itemId
    if source_slot_num is None: return False
    if not self.click_slot(self._open_window_id, source_slot_num, shift=1): return False
    if tool_id is None:
      self.close_window()
      return source_tool_id
    else:
      self.close_window()
      return True

  def put_item_into_chest(self, tool_id, chest_block):
    if self.click_inventory_block(chest_block):
      source_slot_num = self.find_tool(tool_id, window_id=self._open_window_id, inventory_only=True)
      target_slot_num = self.find_tool(-1, window_id=self._open_window_id, storage_only=True)
      if source_slot_num is not None and target_slot_num is not None:
        if self.click_slot(self._open_window_id, source_slot_num):
          if self.click_slot(self._open_window_id, target_slot_num):
            self.close_window()
            return True
    self.close_window()
    return False




















if __name__ == '__main__':
  parser = optparse.OptionParser()
  parser.add_option("-s", "--server", dest="server", default="localhost",
                        help="server", metavar="SERVER")
  parser.add_option("-P", "--port", dest="port", default=25565, type="int",
                        help="port", metavar="PORT")
  parser.add_option("-u", "--user", dest="user",
                        help="user to login as", metavar="USER")
  parser.add_option("-p", "--pass", dest="password",
                        help="password", metavar="PASSWORD")
  parser.add_option("-b", "--bbox", dest="bbox", default='jungle',
                        help="digging bbox", metavar="BBOX")
  parser.add_option("-r", "--return-base", dest="return_base", default='base',
                        help="base to return to for better tools", metavar='BASE')
  parser.add_option('-v', '--verbose', dest='verbose', action='count',
                  help="Increase verbosity (specify multiple times for more)")
  (options, args) = parser.parse_args()

  if options.verbose >= 1: log_level = logging.DEBUG
  else: log_level = logging.INFO
  logging.basicConfig(level=log_level)

  with open('sites.json') as f:
    sites = json.load(f)
    bboxes = sites['bboxes']
    return_bases = sites['return_bases']

  bbox = bboxes[options.bbox]
  home = return_bases[options.return_base] 

  server = options.server
  port = options.port
  password = options.password

  if options.user is not None: 
    username = options.user
  else:
    bot_names = ['peon', 'serf', 'grunt', 'slave', 'drudge', 'farmboy', 'peasant']
    for name in bot_names:
      if not os.path.isfile(os.path.join('/tmp', name)):
        username = name
        break
    else:
      raise Exception('All usernames are logged in')

  if len(args) > 0: 
    cmd = args.pop(0)
    if cmd in ['kill', 'terraform']:
      import scrap
      funcs = {'kill': scrap.kill, 'terraform': scrap.terraform}
      users = {'kill': 'merlin', 'terraform': 'bob'}
      fighting = {'kill': False, 'terraform': True}
      server = 'mc.gmendiola.com'
      bot = MineCraftBot(server, port, users[cmd], password=password, fighting=fighting[cmd])
      bot.WaitFor(lambda: bot._pos.x != 0.0 and bot._pos.y != 0.0)
      time.sleep(5)
      funcs[cmd](bot)
    elif cmd == 'explore':
      username = 'dora'
      bot = MineCraftBot(server, port, username, password=password)
      bot.WaitFor(lambda: bot._pos.x != 0.0 and bot._pos.y != 0.0)
      time.sleep(2)
      import scrap
      scrap.explore(bot)
    if cmd == 'test':
      bot = MineCraftBot(server, port, username, password=password)
      bot.WaitFor(lambda: bot._pos.x != 0.0 and bot._pos.y != 0.0)
      logging.info('bot ready')
    elif cmd == 'help':
      types = [14,15,16,56]
      bot = MineCraftBot(server, port, username, password=password)
      time.sleep(5)
      print bot._pos.xzy()
      start = bot.get_player_position('magahet')
      if start is None:
        x = float(raw_input('x: ').strip())
        z = float(raw_input('z: ').strip())
        start = mc.Xzy(x, z, 0)
      bot.help_find_blocks(start, types=types)
    elif cmd == 'dig':
      bot = MineCraftBot(server, port, username, password=password)
      bot.dig_area(bbox, home=home)

