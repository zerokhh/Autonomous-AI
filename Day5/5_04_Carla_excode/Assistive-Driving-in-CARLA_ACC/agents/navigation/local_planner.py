#!/usr/bin/env python

from enum import Enum
from collections import deque
import random
from matplotlib import pyplot as plt

import carla
from agents.navigation.pid_controller import VehiclePIDController
from agents.navigation.MPC import MPC
from agents.tools.misc import distance_vehicle, draw_waypoints


class RoadOption(Enum):
    """
    RoadOption represents the possible topological configurations when moving from a segment of lane to other.
    'Finite State Machine'
    """
    VOID = -1
    LEFT = 1
    RIGHT = 2
    STRAIGHT = 3
    LANEFOLLOW = 4
    CHANGELANELEFT = 5
    CHANGELANERIGHT = 6


class LocalPlanner(object):
    """
    LocalPlanner implements the basic behavior of following a trajectory of waypoints that is generated on-the-fly.
    The low-level motion of the vehicle is computed by using two PID controllers, one is used for the lateral control
    and the other for the longitudinal control (cruise speed).
    """
    # minimum distance to target waypoint as a percentage (e.g. within 90% of total distance)
    MIN_DISTANCE_PERCENTAGE = 0.7

    def __init__(self, vehicle, opt_dict=None):
        """
        :param vehicle: actor to apply to local planner logic onto
        :param opt_dict: dictionary of arguments with the following semantics:
            dt -- time difference between physics control in seconds. This is typically fixed from server side
                  using the arguments -benchmark -fps=F . In this case dt = 1/F

            target_speed -- desired cruise speed in Km/h

            sampling_radius -- search radius for next waypoints in seconds: e.g. 0.5 seconds ahead

            lateral_control_dict -- dictionary of arguments to setup the lateral PID controller
                                    {'K_P':, 'K_D':, 'K_I':, 'dt'}

            longitudinal_control_dict -- dictionary of arguments to setup the longitudinal PID controller
                                        {'K_P':, 'K_D':, 'K_I':, 'dt'}
        """
        self._vehicle = vehicle
        self._map = self._vehicle.get_world().get_map()

        self._dt = None
        self._target_speed = None
        self._sampling_radius = None
        self._min_distance = None
        self._current_waypoint = None
        self._target_road_option = None
        self._next_waypoints = None
        self.target_waypoint = None
        self._vehicle_controller = None
        self._global_plan = None        
        
        # queue with tuples of (waypoint, RoadOption)
        self._waypoints_queue = deque(maxlen=20000)
        self._buffer_size = 20
        self.waypoint_buffer = deque(maxlen=self._buffer_size)

        # initializing controller
        self.init_controller(opt_dict)

    def reset_vehicle(self):
        self._vehicle = None
        print("Resetting ego-vehicle!")

    def init_controller(self, opt_dict):
        """
        :param opt_dict: dictionary of arguments.
        :return:
        """
        # default params
        self._dt = 1.0 / 20.0      # 1/F
        self._target_speed = 30.0  # Km/h
        args_lateral_dict = {
            'K_P': 1.95,
            'K_D': 0.01,
            'K_I': 1.4,
            'dt': self._dt,
            'control_type': 'PID'}
        args_longitudinal_dict = {
            'K_P': 1.0,
            'K_D': 0,
            'K_I': 1,
            'dt': self._dt}
        
        # parameters overload
        if opt_dict:
            if 'dt' in opt_dict:
                self._dt = opt_dict['dt']
            if 'target_speed' in opt_dict:
                self._target_speed = opt_dict['target_speed']
            if 'lateral_control_dict' in opt_dict:
                args_lateral_dict = opt_dict['lateral_control_dict']
            if 'longitudinal_control_dict' in opt_dict:
                args_longitudinal_dict = opt_dict['longitudinal_control_dict']
        
        self._sampling_radius = self._target_speed * 1 / 3.6  # 1 seconds horizon
        self._min_distance = self._sampling_radius * self.MIN_DISTANCE_PERCENTAGE
        # Controller
        CONTROLLER_TYPE = args_lateral_dict['control_type']
        if CONTROLLER_TYPE == 'MPC':
            self._vehicle_controller = MPC(self._vehicle)
        else:            
            self._vehicle_controller = VehiclePIDController(self._vehicle,
                                                        args_lateral=args_lateral_dict,
                                                        args_longitudinal=args_longitudinal_dict)

        # Plannar
        self._global_plan = False
        # compute initial waypoints
        self._current_waypoint = self._map.get_waypoint(self._vehicle.get_location())
        self._current_waypoint = self._map.get_waypoint(self._vehicle.get_location())
        self._waypoints_queue.append((self._current_waypoint.next(self._sampling_radius)[0], RoadOption.LANEFOLLOW))
        self._target_road_option = RoadOption.LANEFOLLOW
        # fill waypoint trajectory queue
        self._compute_next_waypoints(k=200)

    def set_speed(self, speed):
        """
        :param speed: new target speed in Km/h
        :return:
        """
        self._target_speed = speed

    def _compute_next_waypoints(self, k=1):
        """
        :param k: how many waypoints to compute
        :return:
        """
        # check we do not overflow the queue
        available_entries = self._waypoints_queue.maxlen - len(self._waypoints_queue)
        k = min(available_entries, k)

        for _ in range(k):
            last_waypoint = self._waypoints_queue[-1][0]
            next_waypoints = list(last_waypoint.next(self._sampling_radius))

            if len(next_waypoints) == 1:
                # only one option available ==> lanefollowing
                next_waypoint = next_waypoints[0]
                road_option = RoadOption.LANEFOLLOW
            else:
                # random choice between the possible options
                road_options_list = _retrieve_options(next_waypoints, last_waypoint)
                road_option = random.choice(road_options_list)
                next_waypoint = next_waypoints[road_options_list.index(road_option)]

            self._waypoints_queue.append((next_waypoint, road_option))

    def set_local_plan(self, local_plan):
        # Clear previous queue
        self._waypoints_queue.clear()

        # Add local plan waypoints
        for elem in local_plan:
            self._waypoints_queue.append(elem)
        self._target_road_option = RoadOption.CHANGELANELEFT
        self._global_plan = False

    def add_global_plan(self, current_plan):
        # Add global plan waypoints
        for elem in current_plan:
            self._waypoints_queue.append(elem)
        self._target_road_option = RoadOption.LANEFOLLOW
        self._global_plan = True

    def set_global_plan(self, current_plan):
        # Clear previous queue before setting global plan
        self._waypoints_queue.clear()
        self.add_global_plan(current_plan)
        self._global_plan = True

    def get_global_destination(self):
        return self._waypoints_queue[-1][0]

    def run_step(self, debug=True, target_speed=None):
        """
        Execute one step of local planning which involves running the longitudinal and lateral PID controllers to
        follow the waypoints trajectory.

        :param debug: boolean flag to activate waypoints debugging
        :return:
        """

        # if not enough waypoints in the horizon, add more
        if not self._global_plan and len(self._waypoints_queue) < int(self._waypoints_queue.maxlen * 0.5):
            self._compute_next_waypoints(k=100)

        # Empty queue
        if len(self._waypoints_queue) == 0:
            control = carla.VehicleControl()
            control.steer = 0.0
            control.throttle = 0.0
            control.brake = 1.0
            control.hand_brake = False
            control.manual_gear_shift = False
            return control

        # Buffering the waypoints
        if len(self.waypoint_buffer)<self._buffer_size:
            for i in range(self._buffer_size-len(self.waypoint_buffer)):
                if self._waypoints_queue:
                    self.waypoint_buffer.append(self._waypoints_queue.popleft())
                else:
                    break

        # Control Vehicle
        # current vehicle waypoint
        vehicle_transform = self._vehicle.get_transform()
        self._current_waypoint = self._map.get_waypoint(vehicle_transform.location)
        
        # target waypoint
        self.target_waypoint, self._target_road_option = self.waypoint_buffer[0]
        
        # move using PID controllers
        _waypoints = [i for i,_ in self.waypoint_buffer]
        waypoints = [[points.transform.location.x, points.transform.location.y, points.transform.rotation.yaw] for points in _waypoints]
        # print("waypoints: ", waypoints)    
         
        if target_speed is None:
            target_speed = self._target_speed
        control = self._vehicle_controller.run_step(target_speed, waypoints, self.target_waypoint, self._current_waypoint)

        self.update_buffer()
        
        # Draw waypoints
        if debug:
            draw_waypoints(self._vehicle.get_world(), [self.target_waypoint], 0.8)

        return control

    def soft_stop(self, debug=True):
        """
        Send an light stop command to the vehicle
        :return: control
        """
        control = self.run_step(debug=debug)
        control.throttle = 0.0
        control.brake = 0.1

        return control

    def brake(self, debug=True):
        """
        Send an light stop command to the vehicle
        :return: control
        """
        control = self.run_step(debug=debug)
        control.throttle = 0.0
        control.brake = 1.0

        return control

    def empty_control(self, debug=True):
        control = self.run_step(debug=debug)
        control.throttle = 0.0

        return control

    def done(self):
        vehicle_transform = self._vehicle.get_transform()
        return len(self._waypoints_queue) == 0 and all([distance_vehicle(wp, vehicle_transform) < self._min_distance for wp in self._waypoints_queue])

    def update_buffer(self):
        # purge the queue of obsolete waypoints
        max_index = -1
        for i, (waypoint, _) in enumerate(self.waypoint_buffer):
            if waypoint.transform.location.distance(self._vehicle.get_location()) < self._min_distance:
                max_index = i
        if max_index >= 0:
            for i in range(max_index + 1):
                self.waypoint_buffer.popleft()


def _retrieve_options(list_waypoints, current_waypoint):
    """
    Compute the type of connection between the current active waypoint and the multiple waypoints present in
    list_waypoints. The result is encoded as a list of RoadOption enums.

    :param list_waypoints: list with the possible target waypoints in case of multiple options
    :param current_waypoint: current active waypoint
    :return: list of RoadOption enums representing the type of connection from the active waypoint to each
             candidate in list_waypoints
    """
    options = []
    for next_waypoint in list_waypoints:
        # this is needed because something we are linking to
        # the beggining of an intersection, therefore the
        # variation in angle is small
        next_next_waypoint = next_waypoint.next(3.0)[0]
        link = _compute_connection(current_waypoint, next_next_waypoint)
        options.append(link)

    return options


def _compute_connection(current_waypoint, next_waypoint):
    """
    Compute the type of topological connection between an active waypoint (current_waypoint) and a target waypoint
    (next_waypoint).

    :param current_waypoint: active waypoint
    :param next_waypoint: target waypoint
    :return: the type of topological connection encoded as a RoadOption enum:
             RoadOption.STRAIGHT
             RoadOption.LEFT
             RoadOption.RIGHT
    """
    n = next_waypoint.transform.rotation.yaw
    n = n % 360.0

    c = current_waypoint.transform.rotation.yaw
    c = c % 360.0

    diff_angle = (n - c) % 180.0
    if diff_angle < 1.0:
        return RoadOption.STRAIGHT
    elif diff_angle > 90.0:
        return RoadOption.LEFT
    else:
        return RoadOption.RIGHT
