#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import logging
import argparse


class Logging(logging.getLoggerClass()):
    def __init__(self, loglevel, file):
        self.logger = logging.getLogger()
        self.name = "LOG"
        self.disabled = False
        self.propagate = False
        self._cache = {}
        self.filters = []
        self.handlers = []
        self.setLevel(loglevel.upper())
        self.log_format = (
            "%(asctime)s %(levelname)s %(module)s - %(funcName)s: %(message)s"
        )
        consolelog = logging.StreamHandler()
        consolelog.setFormatter(CustomFormatter(self.log_format))
        self.addHandler(consolelog)
        logging.info("Logging to console")
        if file is not None:
            logging.info("Logging to file: %s" % file)
            try:
                os.makedirs(os.path.dirname(file), exist_ok=True)
            except OSError as e:
                logging.error(e)
                os._exit(1)
            self.logfile = logging.FileHandler(file)
            self.logfile.setFormatter(CustomFormatter(self.log_format))
            self.addHandler(self.logfile)


class CustomFormatter(logging.Formatter):
    grey = "\x1b[38;21m"
    blue = "\x1b[38;5;39m"
    yellow = "\x1b[38;5;226m"
    red = "\x1b[38;5;196m"
    bold_red = "\x1b[31;1m"
    dark_green = "\x1b[38;5;22m"
    light_green = "\x1b[38;5;46m"
    dull_green = "\x1b[38;5;40m"
    green = "\x1b[38;5;45m"
    light_blue = "\x1b[38;5;117m"
    reset = "\x1b[0m"

    def __init__(self, fmt):
        super().__init__()
        self.fmt = fmt
        self.FORMATS = {
            logging.DEBUG: self.light_blue + self.fmt + self.reset,
            logging.INFO: self.dull_green + self.fmt + self.reset,
            logging.WARNING: self.yellow + self.fmt + self.reset,
            logging.ERROR: self.red + self.fmt + self.reset,
            logging.CRITICAL: self.bold_red + self.fmt + self.reset,
        }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)


class LoggingArguments:
    def __init__(self, parser, environment):
        EnvDefault = self.EnvDefault
        parser.add_argument(
            "--log-level",
            action=EnvDefault,
            env=environment,
            envvar="ROSA_BURNER_LOG_LEVEL",
            default="INFO",
        )
        parser.add_argument(
            "--log-file",
            action=EnvDefault,
            env=environment,
            envvar="ROSA_BURNER_LOG_FILE",
            required=False,
        )

    def __getitem__(self, item):
        return self.parameters[item] if item in self.parameters else None

    class EnvDefault(argparse.Action):
        def __init__(self, env, envvar, required=True, default=None, **kwargs):
            if not default and envvar:
                if envvar in env:
                    default = env[envvar]
            if required and default:
                required = False
            super(LoggingArguments.EnvDefault, self).__init__(
                default=default, required=required, **kwargs
            )

        def __call__(self, parser, namespace, values, option_string=None):
            setattr(namespace, self.dest, values)
