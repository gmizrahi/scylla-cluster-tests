"""
Classes that introduce disruption in clusters.
"""

import inspect
import logging
import random
import time

from .data_path import get_data_path
from .log import SDCMAdapter


class Nemesis(object):

    def __init__(self, cluster, termination_event):
        self.cluster = cluster
        self.target_node = None
        logger = logging.getLogger('avocado.test')
        self.log = SDCMAdapter(logger, extra={'prefix': str(self)})
        self.set_target_node()
        self.termination_event = termination_event

    def set_target_node(self):
        non_seed_nodes = [node for node in self.cluster.nodes if not node.is_seed]
        self.target_node = random.choice(non_seed_nodes)
        self.log.info('Current Target: %s', self.target_node)

    def run(self, interval=30, termination_event=None):
        interval *= 60
        while True:
            time.sleep(interval)
            self.disrupt()
            if termination_event is not None:
                if self.termination_event.isSet():
                    self.termination_event = None
                    break
            self.set_target_node()

    def __str__(self):
        return str(self.__class__)

    def disrupt(self):
        raise NotImplementedError('Derived classes must implement disrupt()')

    def disrupt_nodetool_drain(self):
        self.log.info('Drain %s and restart it', self.target_node)
        self.target_node.remoter.run('nodetool -h localhost drain')
        self.target_node.restart()

    def disrupt_nodetool_decommission(self):
        self.log.info('Decommission %s', self.target_node)
        target_node_ip = self.target_node.instance.private_ip_address
        result = self.target_node.remoter.run('nodetool --host localhost '
                                              'decommission')
        self.log.debug("%s duration -> %s s", result.command, result.duration)
        verification_node = random.choice(self.cluster.nodes)
        while verification_node == self.target_node:
            verification_node = random.choice(self.cluster.nodes)

        node_info_list = self.cluster.get_node_info_list(verification_node)
        private_ips = [node_info['ip'] for node_info in node_info_list]
        error_msg = ('Node that was decommissioned {} still in the cluster. '
                     'Cluster status info: {}'.format(self.target_node,
                                                      node_info_list))
        assert target_node_ip not in private_ips, error_msg
        self.cluster.nodes.remove(self.target_node)
        self.target_node.destroy()
        # Replace the node that was terminated.
        new_nodes = self.cluster.add_nodes(count=1)
        self.cluster.wait_for_init(node_list=new_nodes)

    def disrupt_stop_start(self):
        self.log.info('Stop %s then restart it', self.target_node)
        self.target_node.restart()

    def disrupt_kill_scylla_daemon(self):
        self.log.info('Kill all scylla processes in %s', self.target_node)
        kill_cmd = "sudo pkill -9 scylla"
        self.target_node.remoter.run(kill_cmd, ignore_status=True)

        self.target_node.wait_db_down()
        # Let's wait for the target Node to have their services re-started
        self.target_node.wait_db_up()

    def _destroy_data(self):
        # Send the script used to corrupt the DB
        break_scylla = get_data_path('break_scylla.sh')
        self.target_node.remoter.send_files(break_scylla,
                                            "/tmp/break_scylla.sh")

        # corrupt the DB
        self.target_node.remoter.run('chmod +x /tmp/break_scylla.sh')
        self.target_node.remoter.run('/tmp/break_scylla.sh')

        # lennart's systemd will restart scylla let him a bit of time
        self.disrupt_kill_scylla_daemon()

    def disrupt_destroy_data_then_repair(self):
        self.log.info('Destroy user data in %s, then run nodetool repair',
                      self.target_node)
        self._destroy_data()
        # try to save the node
        self.repair_nodetool_repair()

    def disrupt_destroy_data_then_rebuild(self):
        self.log.info('Destroy user data in %s, then run nodetool repair',
                      self.target_node)
        self._destroy_data()
        # try to save the node
        self.repair_nodetool_rebuild()

    def call_random_disrupt_method(self):
        disrupt_methods = [attr[1] for attr in inspect.getmembers(self) if
                           attr[0].startswith('disrupt_') and
                           callable(attr[1])]
        disrupt_method = random.choice(disrupt_methods)
        try:
            disrupt_method()
        except Exception:
            self.log.error('Disrupt method %s failed', disrupt_method,
                           exc_info=True)

    def repair_nodetool_repair(self):
        result = self.target_node.remoter.run('nodetool -h localhost repair')
        self.log.debug('%s duration -> %s s', result.command, result.duration)

    def repair_nodetool_rebuild(self):
        for node in self.cluster.nodes:
            result = node.remoter.run('nodetool -h localhost rebuild')
            self.log.debug('%s duration -> %s s', result.command,
                           result.duration)


def log_time_elapsed(method):
    """
    Log time elapsed for method to run

    :param method: Remote method to wrap.
    :return: Wrapped method.
    """
    def wrapper(*args, **kwargs):
        args[0].log.debug('%s Start', method)
        start_time = time.time()
        try:
            result = method(*args, **kwargs)
        finally:
            elapsed_time = int(time.time() - start_time)
            args[0].log.debug('%s duration -> %s s', method, elapsed_time)
        return result
    return wrapper


class StopStartMonkey(Nemesis):

    @log_time_elapsed
    def disrupt(self):
        self.disrupt_stop_start()


class DrainerMonkey(Nemesis):

    @log_time_elapsed
    def disrupt(self):
        self.disrupt_nodetool_drain()


class CorruptThenRepairMonkey(Nemesis):

    @log_time_elapsed
    def disrupt(self):
        self.disrupt_destroy_data_then_repair()


class CorruptThenRebuildMonkey(Nemesis):

    @log_time_elapsed
    def disrupt(self):
        self.disrupt_destroy_data_then_rebuild()


class DecommissionMonkey(Nemesis):

    @log_time_elapsed
    def disrupt(self):
        self.disrupt_nodetool_decommission()


class ChaosMonkey(Nemesis):

    @log_time_elapsed
    def disrupt(self):
        self.call_random_disrupt_method()
