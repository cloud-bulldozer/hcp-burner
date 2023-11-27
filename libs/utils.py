#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import sys
import shutil
import errno
import string
import signal
import random
import time
import subprocess
import threading
from git import Repo


class Utils:
    def __init__(self, logging):
        self.logging = logging
        self.force_terminate = False

    def set_force_terminate(self, signum, frame):
        self.logging.warning("Captured Ctrl-C, sending exit event to watcher, any cluster install/delete will continue its execution")
        self.force_terminate = True

    def disable_signals(self):
        signal.signal(signal.SIGINT, signal.SIG_IGN)

    def create_path(self, path):
        try:
            self.logging.info(f"Creating directory {path} if it does not exist")
            os.makedirs(path, exist_ok=True)
        except OSError as err:
            if err.errno != errno.EEXIST:
                self.logging.error(err)
                sys.exit("Exiting...")

    def generate_cluster_name_seed(self, seed):
        cluster_name_seed = seed
        allowed_chars = string.ascii_lowercase + string.digits
        for char in seed:
            if char not in allowed_chars:
                self.logging.error(f"Invalid seed for cluster names: {seed}\n. It must contain only lowercase letters and digits.")
                sys.exit("Exiting...")
        random_string = "".join(random.choice(allowed_chars) for j in range(3))
        if len(seed) > 6:
            self.logging.warning(f"Seed for cluster names is too long ({len(seed)}), truncated to {seed[:6]}")
            cluster_name_seed = seed[:6]
        cluster_name_seed = cluster_name_seed + "-" + random_string
        self.logging.info(f"Selected Cluster Name Seed: {cluster_name_seed}")
        return cluster_name_seed

    def verify_cmnd(self, command):
        help_command = command + " help" if command != "terraform" else command + " -h"
        (cmd_code, cmd_out, cmd_err) = self.subprocess_exec(help_command)
        if cmd_code != 0:
            self.logging.error(cmd_out)
            self.logging.error(cmd_err)
            sys.exit("Exiting...")
        else:
            self.logging.info(f"{command} command validated with -h")

    def subprocess_exec(self, command, output_file=None, extra_params={}, log_output=True):
        """
        Function to execute commands on a shell.
        command: command to execute to be passed to subprocess. For example: "ls -l"
        output_file: if defined, file to store output of the command. It will turn return values to None
        extra_params: if defined, any extra param to be passed to Popen function in a mapping format. For example: extra_params={'cwd': '/tmp', 'universal_newlines': False}

        Function call example: exit_code, out, err = common._subprocess_exec("ls -l", extra_params={'cwd': '/tmp', 'universal_newlines': False})
        """
        self.logging.debug(command)
        stdout = None
        stderr = None
        try:
            log_file = open(output_file, "w") if output_file else subprocess.PIPE
            process = subprocess.Popen(command.split(), stdout=log_file, stderr=log_file, **extra_params)
            stdout, stderr = process.communicate()
            if process.returncode != 0 and log_output:
                self.logging.error(f"Failed to execute command: {command}")
                self.logging.error(stdout if stdout else "")
                self.logging.error(stderr if stderr else "")
                if output_file:
                    with open(output_file, "r") as log_read:
                        content = log_read.read()
                        self.logging.error(content)
            return process.returncode, stdout, stderr
        except Exception as err:
            self.logging.error(f"Error executing command: {command}")
            self.logging.error(str(err))
            self.logging.error(stdout if stdout else "")
            self.logging.error(stderr if stderr else "")
            return -1, None, None

    def cleanup_scheduler(self, platform):
        if platform.environment["wait_before_cleanup"] != 0:
            self.logging.info(f"Waiting {platform.environment['wait_before_cleanup']} minutes before starting the cluster deletion")
            time.sleep(platform.environment["wait_before_cleanup"] * 60)
        self.logging.info(f"Attempting to start cleanup process of {len(platform.environment['clusters'])} clusters waiting {platform.environment['delay_between_cleanup']} minutes between each deletion")
        delete_cluster_thread_list = []
        for cluster_name, cluster_info in platform.environment["clusters"].items():
            self.logging.info(f"Attempting to start cleanup process of {cluster_name} on status: {cluster_info['status']}")
            try:
                thread = threading.Thread(
                    target=platform.delete_cluster, args=(platform, cluster_name)
                )
            except Exception as err:
                self.logging.error("Thread creation failed")
                self.logging.error(err)
            delete_cluster_thread_list.append(thread)
            thread.start()
            cluster_info["status"] = "deleting"
            self.logging.debug(
                f"Number of alive threads {threading.active_count()}"
            )
            if platform.environment["delay_between_cleanup"] != 0:
                self.logging.info(
                    f"Waiting {platform.environment['delay_between_cleanup']} minutes before deleting the next cluster"
                )
                time.sleep(platform.environment["delay_between_cleanup"])
        return delete_cluster_thread_list

    # To form the cluster_info dict for cleanup funtions
    # It will be called only when --cleanup-clusters without --install-clusters
    def get_cluster_info(self, platform):
        loop_counter = 0
        while loop_counter < platform.environment["cluster_count"]:
            loop_counter += 1
            cluster_name = platform.environment["cluster_name_seed"] + "-" + str(loop_counter).zfill(4)
            platform.environment["clusters"][cluster_name] = {}
            platform.environment["clusters"][cluster_name]["metadata"] = platform.get_metadata(cluster_name)
            platform.environment["clusters"][cluster_name]["status"] = platform.environment["clusters"][cluster_name]["metadata"]["status"]
            platform.environment["clusters"][cluster_name]["path"] = platform.environment["path"] + "/" + cluster_name
        return platform

    def load_scheduler(self, platform):
        load_thread_list = []
        self.logging.info(f"Attempting to start {platform.environment['load']['executor']} {platform.environment['load']['workload']} load process on {len(platform.environment['clusters'])} clusters")
        for cluster_name, cluster_info in platform.environment["clusters"].items():
            self.logging.debug(cluster_info)
            if cluster_info['status'] == "ready":
                self.logging.info(f"Attempting to start load process on {cluster_name}")
                try:
                    thread = threading.Thread(target=self.cluster_load, args=(platform, cluster_name))
                except Exception as err:
                    self.logging.error("Thread creation failed")
                    self.logging.error(err)
                load_thread_list.append(thread)
                thread.start()
        return load_thread_list

    def install_scheduler(self, platform):
        self.logging.info(
            f"Attempting to start {platform.environment['cluster_count']} clusters with {platform.environment['batch_size']} batch size"
        )
        cluster_thread_list = []
        batch_count = 0
        loop_counter = 0
        try:
            while loop_counter < platform.environment["cluster_count"]:
                self.logging.debug(platform.environment["clusters"])
                if self.force_terminate:
                    loop_counter += 1
                else:
                    create_cluster = False
                    if platform.environment["batch_size"] != 0:
                        if platform.environment["delay_between_batch"] is None:
                            # We add 2 to the batch size. 1 for the main thread and 1 for the watcher
                            while (platform.environment["batch_size"] + 2) <= threading.active_count():
                                # Wait for thread count to drop before creating another
                                time.sleep(1)
                            loop_counter += 1
                            create_cluster = True
                        elif batch_count >= platform.environment["batch_size"]:
                            time.sleep(platform.environment["delay_between_batch"])
                            batch_count = 0
                        else:
                            batch_count += 1
                            loop_counter += 1
                            create_cluster = True
                    else:
                        loop_counter += 1
                        create_cluster = True
                    if create_cluster:
                        if platform.environment["workers"].isdigit():
                            cluster_workers = int(platform.environment["workers"])
                        else:
                            cluster_workers = int(platform.environment["workers"].split(",")[(loop_counter - 1) % len(platform.environment["workers"].split(","))])
                        cluster_name = platform.environment["cluster_name_seed"] + "-" + str(loop_counter).zfill(4)
                        platform.environment["clusters"][cluster_name] = {}
                        try:
                            platform.environment["clusters"][cluster_name]["workers"] = cluster_workers
                            platform.environment["clusters"][cluster_name]["workers_wait_time"] = platform.environment["workers_wait_time"]
                            platform.environment["clusters"][cluster_name]["index"] = loop_counter - 1
                            thread = threading.Thread(target=platform.create_cluster, args=(platform, cluster_name))
                            platform.environment["clusters"][cluster_name]["status"] = "creating"
                        except Exception as err:
                            self.logging.error(f"Failed to create cluster {cluster_name}")
                            self.logging.error(err)
                            platform.environment["clusters"][cluster_name]["status"] = "thread_failed"
                        cluster_thread_list.append(thread)
                        thread.start()
                        self.logging.debug("Number of alive threads %d" % threading.active_count())
        except Exception as err:
            self.logging.error(err)
            self.logging.error("Thread creation failed")
        return cluster_thread_list

    def cluster_load(self, platform, cluster_name, load=""):
        load_env = os.environ.copy()
        my_path = platform.environment['clusters'][cluster_name]['path']
        load_env["KUBECONFIG"] = platform.environment.get('clusters', {}).get(cluster_name, {}).get('kubeconfig', "")
        load_env["MC_KUBECONFIG"] = platform.environment.get("mc_kubeconfig", "")
        if not os.path.exists(my_path + '/workload'):
            self.logging.info(f"Cloning workload repo {platform.environment['load']['repo']} on {my_path}/workload")
            Repo.clone_from(platform.environment['load']['repo'], my_path + '/workload')
        # Copy executor to the local folder because we shaw in the past that we cannot use kube-burner with multiple executions at the same time
        shutil.copy2(platform.environment['load']['executor'], my_path)
        load_env["ITERATIONS"] = str(platform.environment['clusters'][cluster_name]['workers'] * platform.environment['load']['jobs'])
        load_env["EXTRA_FLAGS"] = "--churn-duration=" + platform.environment['load']['duration'] + " --churn-percent=10 --churn-delay=30s --timeout=24h"
        # if es_url is not None:
        #     load_env["ES_SERVER"] = es_url
        load_env["LOG_LEVEL"] = "debug"
        load_env["WORKLOAD"] = load if load != "" else platform.environment['load']['workload']
        log_file = load if load != "" else platform.environment['load']['workload']
        load_env["KUBE_DIR"] = my_path
        if not self.force_terminate:
            load_code, load_out, load_err = self.subprocess_exec('./run.sh', my_path + '/' + log_file + '.log', extra_params={'cwd': my_path + "/workload/" + platform.environment['load']['script_path'], 'env': load_env})
            if load_code != 0:
                self.logging.error(f"Failed to execute workload {platform.environment['load']['script_path'] + '/run.sh'} on {cluster_name}")
        else:
            self.logging.warning(f"Not starting workload on {cluster_name} after capturing Ctrl-C")
