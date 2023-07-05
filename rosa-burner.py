#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import sys
import time
import importlib
import threading
from libs.arguments import Arguments
from libs.logging import Logging
from libs.elasticsearch import Elasticsearch
from libs.utils import Utils

if __name__ == "__main__":
    arguments = Arguments(os.environ)
    logging = Logging(arguments["log_level"], arguments["log_file"])
    es = Elasticsearch(logging, arguments["es_url"], arguments["es_index"], arguments["es_insecure"], arguments["es_index_retry"]) if arguments["es_url"] else None
    utils = Utils(logging)

    logging.info(f"Detected {arguments['platform']} as platform")
    try:
        if arguments["subplatform"]:
            logging.info(
                f"Detected {arguments['subplatform']} as subplatform, loading its module..."
            )
            platform_module_path = (
                "libs.platforms."
                + arguments["platform"]
                + "."
                + arguments["subplatform"]
                + "."
                + arguments["subplatform"]
            )
            platform_module = importlib.import_module(platform_module_path)
            PlatformClass = getattr(
                platform_module, arguments["subplatform"].capitalize()
            )
            platform = PlatformClass(arguments, logging, utils)
        else:
            logging.info(
                f"Subplatform not detected, loading {arguments['platform']} module..."
            )
            platform_module_path = (
                "libs.platforms." + arguments["platform"] + "." + arguments["platform"]
            )
            platform_module = importlib.import_module(platform_module_path)
            PlatformClass = getattr(platform_module, arguments["platform"].capitalize())
            platform = PlatformClass(arguments, logging, utils)
    except ImportError as err:
        logging.error("Module not found)")
        logging.error(err)
        sys.exit("Exiting...")
    except AttributeError as err:
        logging.error("Invalid platform class in module")
        logging.error(err)
        sys.exit("Exiting...")

    logging.info(
        f"Verifying external binaries required by the {arguments['platform']} platform"
    )
    for command in platform.environment["commands"]:
        utils.verify_cmnd(command)

    platform.initialize()

    watcher = threading.Thread(target=platform.watcher)
    watcher.daemon = True
    watcher.start()

    install_threads = utils._install_scheduler(platform)
    logging.info(f"{len(install_threads)} threads created for installing clusters. Waiting for them to finish")
    for thread in install_threads:
        try:
            thread.join()
        except RuntimeError as err:
            if "cannot join current thread" in err.args[0]:
                # catchs main thread
                continue
            else:
                raise
    watcher.join()

    if platform.environment['load']:
        # Prometheus takes a lot of time to start after all nodes are ready. we maybe needs to increase this sleep in the future
        logging.info("Waiting 5 minutes to allow all clusters to create all pods")
        time.sleep(300)
        load_threads = utils.load_scheduler(platform)
        logging.info(f"{len(load_threads)} threads created to execute workloads. Waiting for them to finish")
        for thread in load_threads:
            try:
                thread.join()
            except RuntimeError as err:
                if "cannot join current thread" in err.args[0]:
                    # catchs main thread
                    continue
                else:
                    raise

    if platform.environment["cleanup_clusters"]:
        delete_threads = utils._cleanup_scheduler(platform)
        logging.info(f"{len(delete_threads)} threads created for deleting clusters. Waiting for them to finish")
        for thread in delete_threads:
            try:
                thread.join()
            except RuntimeError as err:
                if "cannot join current thread" in err.args[0]:
                    # catchs main thread
                    continue
                else:
                    raise

    platform.platform_cleanup()

    # utils.test_recap(platform)
