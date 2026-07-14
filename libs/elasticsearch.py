#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Module to set connection to ElasticSearch/OpenSearch and functions to upload documents
"""
import argparse
import configparser
import sys
import ssl
from urllib.parse import urlparse, unquote
import urllib3

OS = None
ES = None
try:
    from opensearchpy import OpenSearch as OS
    from opensearchpy.exceptions import NotFoundError
    _USE_OPENSEARCH = True
except ImportError:
    from elasticsearch import Elasticsearch as ES
    from elasticsearch.exceptions import NotFoundError
    _USE_OPENSEARCH = False


class Elasticsearch:
    """ES/OpenSearch Class"""

    def __init__(self, logging, url, index, insecure, retries):
        super().__init__()
        self.logging = logging
        self.index = index

        self.logging.info("Initializing Elasticsearch/OpenSearch Connector...")

        parsed = urlparse(url)
        auth = None
        if parsed.username and parsed.password:
            auth = (unquote(parsed.username), unquote(parsed.password))
            clean_url = f"{parsed.scheme}://{parsed.hostname}:{parsed.port or 443}"
        else:
            clean_url = url

        if url.startswith("https://"):
            self.logging.debug("Setting Connector with SSL...")
            ssl_ctx = ssl.create_default_context()
            if str(insecure).lower() == "true":
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                self.logging.debug("Setting Connector with SSL unverified...")
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE

            if _USE_OPENSEARCH:
                self.elastic = OS(
                    clean_url,
                    http_auth=auth,
                    ssl_context=ssl_ctx,
                    verify_certs=False,
                    max_retries=retries,
                    retry_on_timeout=True,
                )
            else:
                kwargs = {"ssl_context": ssl_ctx, "verify_certs": False, "max_retries": retries, "retry_on_timeout": True}
                if auth:
                    kwargs["basic_auth"] = auth
                self.elastic = ES(clean_url, **kwargs)

        elif url.startswith("http://"):
            if _USE_OPENSEARCH:
                self.elastic = OS(clean_url, http_auth=auth, max_retries=retries, retry_on_timeout=True)
            else:
                kwargs = {"max_retries": retries, "retry_on_timeout": True}
                if auth:
                    kwargs["basic_auth"] = auth
                self.elastic = ES(clean_url, **kwargs)
        else:
            self.logging.error(f"Failed to initialize with url {url}. It must start with http(s)://")
            sys.exit("Exiting...")

        self.logging.debug("Testing connection")
        if self.elastic.ping():
            self.logging.debug("Version: " + self.elastic.info()["version"]["number"])
            if not self._check_index():
                self.logging.error(f"Index {index} does not exist")
                sys.exit("Exiting...")
        else:
            self.logging.error(f"Cannot establish connection with {clean_url}")
            sys.exit("Exiting...")

        backend = "opensearch-py" if _USE_OPENSEARCH else "elasticsearch"
        self.logging.info(f"Connected using {backend}")

    def _check_index(self):
        try:
            return self.elastic.indices.exists(index=self.index)
        except NotFoundError:
            return False

    def index_metadata(self, metadata):
        try:
            hosts = self.elastic.transport.hosts if hasattr(self.elastic.transport, 'hosts') else [{"host": "unknown"}]
            self.logging.debug(f"Indexing data on {hosts[0]}/{self.index}")
        except Exception:
            self.logging.debug(f"Indexing data on {self.index}")
        self.logging.debug(metadata)
        try:
            self.elastic.index(index=self.index, body=metadata)
        except Exception as err:
            self.logging.error(err)
            self.logging.error(f"Failed to index data on {self.index}")
            self.logging.error(metadata)


class ElasticArguments:
    def __init__(self, parser, config_file, environment):
        EnvDefault = self.EnvDefault
        parser.add_argument("--es-url", action=EnvDefault, env=environment, envvar="HCP_BURNER_ES_URL", help="Elasticsearch URL")
        parser.add_argument("--es-index", action=EnvDefault, env=environment, envvar="HCP_BURNER_ES_INDEX", help="Elasticsearch Index", default="hcp-burner")
        parser.add_argument("--es-index-retry", action=EnvDefault, env=environment, envvar="HCP_BURNER_ES_INDEX_RETRY", type=int, help="Number of retries when index operation fails", default=5)
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
