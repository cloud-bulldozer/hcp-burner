#!/usr/bin/env bash
python3 -m venv .hcp-burner
source .hcp-burner/bin/activate
pip3 install -q --upgrade pip
pip3 install -r requirements.txt
