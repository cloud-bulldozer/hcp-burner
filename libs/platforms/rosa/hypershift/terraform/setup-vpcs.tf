variable "cluster_count" {
  type        = number
  description = "The number of VPCs to create"
  default     = 1
}

variable "cluster_name_seed" {
  type        = string
  description = "The name used to create the VPCs"
  default     = "rosa-cluster"
}

variable "aws_region" {
  type        = string
  description = "The region to create the ROSA cluster in"
  default     = "us-east-2"
}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  selected_azs = slice(data.aws_availability_zones.available.names, 0, 3)
}

provider "aws" {
  region = var.aws_region
}

module "vpc" {
  count   = var.cluster_count
  source  = "terraform-aws-modules/vpc/aws"
  version = "5.0.0"
  azs     = local.selected_azs
  name    = "vpc-${var.cluster_name_seed}-${format("%04d", count.index + 1)}"
  cidr    = "10.0.0.0/16"

  private_subnets = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
  public_subnets  = ["10.0.101.0/24", "10.0.102.0/24", "10.0.103.0/24"]

  enable_nat_gateway            = true
  single_nat_gateway            = false
  one_nat_gateway_per_az        = true
  enable_dns_hostnames          = true
  enable_dns_support            = true
  manage_default_security_group = false
}

resource "aws_eip" "nat" {
  domain = "vpc"
  depends_on = [module.vpc]
}

output "vpc-id" {
  value = module.vpc.*.vpc_id
}

output "cluster-private-subnets" {
  value = [for vpc in module.vpc : vpc.private_subnets]
}

output "cluster-public-subnets" {
  value = [for vpc in module.vpc : vpc.public_subnets]
}
