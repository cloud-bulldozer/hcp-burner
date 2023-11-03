#
# Copyright (c) 2023 Red Hat, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 4.20.0"
    }
    rhcs = {
      version = ">= 1.1.0"
      source  = "terraform-redhat/rhcs"
    }
  }
}
provider "rhcs" {
  token = var.token
  url   = var.url
}

locals {
  path = coalesce(var.path, "/")
  sts_roles = {
    role_arn         = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role${local.path}${var.account_role_prefix}-Installer-Role",
    support_role_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role${local.path}${var.account_role_prefix}-Support-Role",
    instance_iam_roles = {
      master_role_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role${local.path}${var.account_role_prefix}-ControlPlane-Role",
      worker_role_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role${local.path}${var.account_role_prefix}-Worker-Role"
    },
    operator_role_prefix = var.operator_role_prefix,
    oidc_config_id       = var.oidc_config_id
  }
}

data "aws_caller_identity" "current" {
}

locals {
  openshift_version = var.openshift_version != null ? var.openshift_version : null
}

resource "rhcs_cluster_rosa_classic" "rosa_sts_cluster" {
  count                = var.clusters_per_apply
  name                 = "${var.cluster_name}-${format("%04d", var.loop_factor + count.index + 1)}"
  cloud_region         = var.cloud_region
  aws_account_id       = data.aws_caller_identity.current.account_id
  availability_zones   = var.availability_zones
  replicas             = var.replicas
  autoscaling_enabled  = var.autoscaling_enabled
  min_replicas         = var.min_replicas
  max_replicas         = var.max_replicas
  version              = local.openshift_version
  compute_machine_type = var.compute_machine_type
  properties = {
    rosa_creator_arn = data.aws_caller_identity.current.arn
  }
  sts                      = local.sts_roles
  wait_for_create_complete = false
  disable_waiting_in_destroy = true
}
