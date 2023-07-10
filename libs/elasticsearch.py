#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Module to set connection to ElasticSearch and functions to upload documents
"""
import argparse
import configparser
import sys
import ssl
from elasticsearch import Elasticsearch as ES
from elasticsearch.exceptions import NotFoundError
import urllib3
from urllib3.util import Retry


class Elasticsearch:
    """ES Class"""

    def __init__(self, logging, url, index, insecure, retries):
        super().__init__()
        self.logging = logging
        self.index = index

        retry_on_timeout = True
        retry_strategy = Retry(total=retries, backoff_factor=0.1)
        retry_params = {
            "retry_on_timeout": retry_on_timeout,
            "retry": retry_strategy,
        }

        self.logging.info("Initializing Elasticsearch Connector...")
        if url.startswith("https://"):
            self.logging.debug("Setting Elasticsearch Connector with SSL...")
            ssl_ctx = ssl.create_default_context()
            if str(insecure).lower() == "true":
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                self.logging.debug("Setting Elasticsearch Connector with SSL unverified...")
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE
            self.elastic = ES(url, ssl_context=ssl_ctx, verify_certs=False, **retry_params)
        elif url.startswith("http://"):
            self.elastic = ES(url, **retry_params)
        else:
            self.logging.error(f"Failed to initialize Elasticsearch with url {url}. It must start with http(s)://")
            sys.exit("Exiting...")
        self.logging.debug("Testing Elasticsearch connection")
        if self.elastic.ping():
            self.logging.debug("Version: " + self.elastic.info()["version"]["number"])
            if not self._check_index():
                self.logging.error(f"ES index {index} do not exists")
                sys.exit("Exiting...")
        else:
            self.logging.error(f"Cannot stablish connection with {url}")
            sys.exit("Exiting...")

    def _check_index(self):
        try:
            return self.elastic.indices.exists(index=self.index)
        except NotFoundError:
            return False

    def index_metadata(self, metadata):
        self.logging.debug(f"Indexing data on {self.elastic.transport.hosts[0]}/{self.index}")
        self.logging.debug(metadata)
        try:
            self.elastic.index(index=self.index, body=metadata)
        except Exception as err:
            self.logging.error(err)
            self.logging.error(f"Failed to index data on on {self.elastic.transport.hosts[0]}/{self.elastic.info().get('index')})")
            self.logging.error(metadata)


class ElasticArguments:
    def __init__(self, parser, config_file, environment):
        EnvDefault = self.EnvDefault
        parser.add_argument("--es-url", action=EnvDefault, env=environment, envvar="ROSA_BURNER_ES_URL", help="Elasticsearch URL")
        parser.add_argument("--es-index", action=EnvDefault, env=environment, envvar="ROSA_BURNER_ES_INDEX", help="Elasticsearch Index", default="rosa-burner")
        parser.add_argument("--es-index-retry", action=EnvDefault, env=environment, envvar="ROSA_BURNER_ES_INDEX_RETRY", type=int, help="Number of retries when index operation fails", default=5)
        parser.add_argument("--es-insecure", action="store_true", help="Bypass cert verification on SSL connections")

        args, unknown_args = parser.parse_known_args()

        if config_file:
            config = configparser.ConfigParser()
            config.read(config_file)
            defaults = {}
            defaults.update(dict(config.items("Elasticsearch")))
            parser.set_defaults(**defaults)

    # def __getitem__(self, item):
    #     return self.parameters[item] if item in self.parameters else None

    class EnvDefault(argparse.Action):
        """Argument passed has preference over the envvar"""

        def __init__(self, env, envvar, default=None, **kwargs):
            default = env[envvar] if envvar in env else default
            super(ElasticArguments.EnvDefault, self).__init__(
                default=default, **kwargs
            )

        def __call__(self, parser, namespace, values, option_string=None):
            setattr(namespace, self.dest, values)
