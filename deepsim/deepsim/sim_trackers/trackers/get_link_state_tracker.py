#################################################################################
#   Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.          #
#                                                                               #
#   Licensed under the Apache License, Version 2.0 (the "License").             #
#   You may not use this file except in compliance with the License.            #
#   You may obtain a copy of the License at                                     #
#                                                                               #
#       http://www.apache.org/licenses/LICENSE-2.0                              #
#                                                                               #
#   Unless required by applicable law or agreed to in writing, software         #
#   distributed under the License is distributed on an "AS IS" BASIS,           #
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.    #
#   See the License for the specific language governing permissions and         #
#   limitations under the License.                                              #
#################################################################################
"""A class for get_link_state tracker."""
from collections import OrderedDict
import threading
from typing import Optional, Collection, Dict, Tuple

from deepsim.exception import DeepSimException
from deepsim.gazebo.constants import GazeboServiceName
from deepsim.sim_trackers.tracker import TrackerInterface
from deepsim.sim_trackers.tracker_manager import TrackerManager
from deepsim.core.twist import Twist
from deepsim.core.pose import Pose
from deepsim.core.link_state import LinkState
from deepsim.ros.service_proxy_wrapper import ServiceProxyWrapper
import deepsim.sim_trackers.constants as consts

from deepsim_msgs.srv import (
    GetLinkStates,
    GetAllLinkStates,
    GetAllLinkStatesRequest
)
from rosgraph_msgs.msg import Clock


class GetLinkStateTracker(TrackerInterface):
    """
    GetLinkState Tracker class
    """
    _instance = None
    _instance_lock = threading.RLock()

    @staticmethod
    def get_instance() -> 'GetLinkStateTracker':
        """
        Method for getting a reference to the GetLinkState Tracker object

        Returns:
            GetLinkStateTracker: GetLinkStateTracker instance
        """
        with GetLinkStateTracker._instance_lock:
            if GetLinkStateTracker._instance is None:
                GetLinkStateTracker()
            return GetLinkStateTracker._instance

    def __init__(self, is_singleton=True) -> None:
        """
        Initialize GetLinkState Tracker

        Args:
            is_singleton (bool): the flag whether to instantiate as singleton or not (Should only be used by unit-test)
        """
        if is_singleton:
            if GetLinkStateTracker._instance is not None:
                raise RuntimeError("Attempting to construct multiple GetLinkState Tracker")
            GetLinkStateTracker._instance = self

        self._lock = threading.RLock()
        self._link_map = {}

        self._get_link_states = ServiceProxyWrapper(GazeboServiceName.GET_LINK_STATES, GetLinkStates)
        self._get_all_link_states = ServiceProxyWrapper(GazeboServiceName.GET_ALL_LINK_STATES, GetAllLinkStates)
        TrackerManager.get_instance().add(tracker=self, priority=consts.TrackerPriority.HIGH)

    def on_update_tracker(self, delta_time: float, sim_time: Clock) -> None:
        """
        Update all link states tracking to gazebo

        Args:
            delta_time (float): delta time
            sim_time (Clock): simulation time
        """
        link_map = {}
        res = self._get_all_link_states(GetAllLinkStatesRequest())
        if res.success:
            for link_state in res.link_states:
                link_map[link_state.link_name] = LinkState.from_ros(link_state)
            with self._lock:
                self._link_map = link_map

    def get_link_state(self, name: str, reference_frame: Optional[str] = None,
                       blocking: bool = False) -> LinkState:
        """
        Return link state of given name of the link.

        Args:
            name (str): name of the link.
            reference_frame (Optional[str]): the reference frame
            blocking (bool): flag to block or not

        Returns:
            LinkState: link state
        """
        with self._lock:
            if blocking or name not in self._link_map or reference_frame:
                # if name doesn't exist in the map or if there is reference frame specified
                # then manually retrieve link_state
                reference_frame = reference_frame if reference_frame else ''
                res = self._get_link_states([name], [reference_frame])
                if res.success and res.status[0]:
                    link_state = res.link_states[0]
                    return LinkState(link_state.link_name,
                                     pose=Pose.from_ros(link_state.pose),
                                     twist=Twist.from_ros(link_state.twist),
                                     reference_frame=link_state.reference_frame)
                else:
                    err_message = res.messages[0] if res.messages else ''
                    raise DeepSimException("get_link_state failed: {} ({})".format(res.status_message,
                                                                                   err_message))
            else:
                return self._link_map[name].copy()

    def get_link_states(self, names: Collection[str], reference_frames: Optional[Collection[str]] = None,
                        blocking: bool = False) -> Dict[Tuple[str, str], LinkState]:
        """
        Return link state of given name of the link.
        * This method will ignore link_state that is failed to retrieve.

        Args:
            names (Collection[str]): name of the link.
            reference_frames (Optional[Collection[str]]): the reference frames
            blocking (bool): flag to block or not

        Returns:
            Dict[Tuple[str, str], LinkState]: {(name, reference_frame): link_state}
        """

        links = OrderedDict()
        if reference_frames is None:
            reference_frames = ['' for _ in names]
        if len(names) != len(reference_frames):
            err_msg = "names ({}) and reference_frames ({}) must be equal size!".format(len(names),
                                                                                        len(reference_frames))
            raise ValueError(err_msg)
        query_names = []
        query_reference_frames = []
        with self._lock:
            for name, reference_frame in zip(names, reference_frames):
                key = (name, reference_frame)
                if blocking or name not in self._link_map or reference_frame:
                    query_names.append(name)
                    query_reference_frames.append(reference_frame)
                    links[key] = LinkState()
                else:
                    links[key] = self._link_map[name].copy()

        if len(query_names) > 0 and len(query_reference_frames) > 0:
            res = self._get_link_states(query_names, query_reference_frames)
            if res.success:
                for idx, link_state in enumerate(res.link_states):
                    key = (query_names[idx], query_reference_frames[idx])
                    if res.status[idx]:
                        links[key] = LinkState(link_state.link_name,
                                               pose=Pose.from_ros(link_state.pose),
                                               twist=Twist.from_ros(link_state.twist),
                                               reference_frame=link_state.reference_frame)
                    else:
                        links[key] = None
            else:
                raise DeepSimException("get_link_state failed: {}".format(res.status_message))
        return links

    def set_link_state(self, link_state: LinkState) -> None:
        """
        Set given LinkState to cache.

        Args:
            link_state (LinkState): link state to cache.
        """
        with self._lock:
            self._link_map[link_state.link_name] = link_state.copy()
