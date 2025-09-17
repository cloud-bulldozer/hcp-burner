import boto3
import argparse
import logging
from botocore.exceptions import ClientError
from typing import List, Dict

# --- Basic Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def get_vpc_dependencies(ec2_client, elbv2_client, elb_client, vpc_id: str) -> Dict:
    """Gathers all specified dependencies for a given VPC, including load balancers and endpoints."""
    dependencies = {
        "Subnets": [], "RouteTables": [], "InternetGateways": [],
        "NatGateways": [], "NetworkAcls": [], "SecurityGroups": [],
        "LoadBalancersV2": [], "TargetGroups": [], "ClassicLoadBalancers": [],
        "VpcEndpoints": []
    }
    try:
        # Standard VPC resources
        dependencies["Subnets"] = ec2_client.describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])['Subnets']
        dependencies["RouteTables"] = ec2_client.describe_route_tables(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])['RouteTables']
        dependencies["InternetGateways"] = ec2_client.describe_internet_gateways(Filters=[{'Name': 'attachment.vpc-id', 'Values': [vpc_id]}])['InternetGateways']
        dependencies["NatGateways"] = ec2_client.describe_nat_gateways(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])['NatGateways']
        dependencies["NetworkAcls"] = ec2_client.describe_network_acls(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])['NetworkAcls']
        dependencies["SecurityGroups"] = ec2_client.describe_security_groups(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])['SecurityGroups']
        dependencies["VpcEndpoints"] = ec2_client.describe_vpc_endpoints(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])['VpcEndpoints']

        # Load Balancer resources
        # Note: describe calls for LBs don't have a direct VPC filter, so we filter post-call.
        all_lbs_v2 = elbv2_client.describe_load_balancers().get('LoadBalancers', [])
        dependencies["LoadBalancersV2"] = [lb for lb in all_lbs_v2 if lb.get('VpcId') == vpc_id]

        all_tgs = elbv2_client.describe_target_groups().get('TargetGroups', [])
        dependencies["TargetGroups"] = [tg for tg in all_tgs if tg.get('VpcId') == vpc_id]

        all_clbs = elb_client.describe_load_balancers().get('LoadBalancerDescriptions', [])
        dependencies["ClassicLoadBalancers"] = [clb for clb in all_clbs if clb.get('VPCId') == vpc_id]

    except ClientError as e:
        logging.error(f"Could not retrieve dependencies for {vpc_id}: {e}")
    return dependencies


def delete_vpc_and_dependencies(ec2_client, elbv2_client, elb_client, vpc_id: str, dependencies: Dict, dry_run: bool):
    """
    Deletes a VPC and its dependencies in the correct, robust order.
    """
    logging.info(f"--- Processing VPC for deletion: {vpc_id} ---")

    # Step 1: Delete Load Balancers and Target Groups
    # These often have network interfaces in the subnets and must be deleted first.
    lb_v2_arns = [lb['LoadBalancerArn'] for lb in dependencies['LoadBalancersV2']]
    if lb_v2_arns:
        for arn in lb_v2_arns:
            logging.info(f"Deleting Load Balancer (v2/ALB/NLB): {arn}")
            if not dry_run:
                try:
                    elbv2_client.delete_load_balancer(LoadBalancerArn=arn)
                except ClientError as e:
                    logging.error(f"Could not delete Load Balancer {arn}: {e}")
            else:
                logging.info(f"[DRY RUN] Would delete Load Balancer (v2): {arn}")
        if not dry_run and lb_v2_arns:
            logging.info("Waiting for v2 Load Balancer(s) to be deleted...")
            try:
                waiter = elbv2_client.get_waiter('load_balancers_deleted')
                waiter.wait(LoadBalancerArns=lb_v2_arns, WaiterConfig={'Delay': 15, 'MaxAttempts': 40})
                logging.info("Load Balancer(s) (v2) successfully deleted.")
            except Exception as e:
                logging.error(f"Error waiting for v2 load balancers to delete: {e}")

    tg_arns = [tg['TargetGroupArn'] for tg in dependencies['TargetGroups']]
    if tg_arns:
        for arn in tg_arns:
            logging.info(f"Deleting Target Group: {arn}")
            if not dry_run:
                try:
                    elbv2_client.delete_target_group(TargetGroupArn=arn)
                except ClientError as e:
                    logging.error(f"Could not delete Target Group {arn}: {e}")
            else:
                logging.info(f"[DRY RUN] Would delete Target Group: {arn}")

    clb_names = [clb['LoadBalancerName'] for clb in dependencies['ClassicLoadBalancers']]
    if clb_names:
        for name in clb_names:
            logging.info(f"Deleting Classic Load Balancer: {name}")
            if not dry_run:
                try:
                    elb_client.delete_load_balancer(LoadBalancerName=name)
                except ClientError as e:
                    logging.error(f"Could not delete Classic Load Balancer {name}: {e}")
            else:
                logging.info(f"[DRY RUN] Would delete Classic Load Balancer: {name}")

    # Step 2: High-level networking (NAT Gateways, Endpoints, etc.)
    nat_gateway_ids = [ng['NatGatewayId'] for ng in dependencies['NatGateways'] if ng['State'] != 'deleted']
    if nat_gateway_ids:
        for ng_id in nat_gateway_ids:
            logging.info(f"Deleting NAT Gateway: {ng_id}")
            if not dry_run:
                try:
                    ec2_client.delete_nat_gateway(NatGatewayId=ng_id)
                except ClientError as e:
                    logging.error(f"Could not delete NAT Gateway {ng_id}: {e}")
            else:
                logging.info(f"[DRY RUN] Would delete NAT Gateway: {ng_id}")
        if not dry_run:
            logging.info("Waiting for NAT Gateway(s) to be deleted...")
            try:
                waiter = ec2_client.get_waiter('nat_gateway_deleted')
                waiter.wait(NatGatewayIds=nat_gateway_ids, WaiterConfig={'Delay': 15, 'MaxAttempts': 40})
                logging.info("NAT Gateway(s) successfully deleted.")
            except Exception as e:
                logging.error(f"Error waiting for NAT gateways to delete: {e}")

    vpc_endpoint_ids = [ep['VpcEndpointId'] for ep in dependencies['VpcEndpoints']]
    if vpc_endpoint_ids:
        logging.info(f"Deleting {len(vpc_endpoint_ids)} VPC Endpoint(s)...")
        if not dry_run:
            try:
                ec2_client.delete_vpc_endpoints(VpcEndpointIds=vpc_endpoint_ids)
                logging.info("Successfully initiated deletion for VPC Endpoint(s).")
            except ClientError as e:
                logging.error(f"Could not delete VPC Endpoints: {e}")
        else:
            for ep_id in vpc_endpoint_ids:
                logging.info(f"[DRY RUN] Would delete VPC Endpoint: {ep_id}")

    # Step 3: Detach and Delete Gateways
    for igw in dependencies['InternetGateways']:
        igw_id = igw['InternetGatewayId']
        logging.info(f"Detaching and deleting Internet Gateway: {igw_id}")
        if not dry_run:
            try:
                ec2_client.detach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
                ec2_client.delete_internet_gateway(InternetGatewayId=igw_id)
            except ClientError as e:
                logging.error(f"Could not delete IGW {igw_id}: {e}")
        else:
            logging.info(f"[DRY RUN] Would detach and delete Internet Gateway: {igw_id}")

    # Step 4: Revoke Security Group Rules
    non_default_sgs = [sg for sg in dependencies['SecurityGroups'] if sg['GroupName'] != 'default']
    for sg in non_default_sgs:
        sg_id = sg['GroupId']
        logging.info(f"Revoking all rules from Security Group: {sg_id}")
        if not dry_run:
            try:
                if sg.get('IpPermissions'):
                    ec2_client.revoke_security_group_ingress(GroupId=sg_id, IpPermissions=sg['IpPermissions'])
                if sg.get('IpPermissionsEgress'):
                    ec2_client.revoke_security_group_egress(GroupId=sg_id, IpPermissions=sg['IpPermissionsEgress'])
            except ClientError as e:
                logging.error(f"Could not revoke rules from SG {sg_id}: {e}")
        else:
            logging.info(f"[DRY RUN] Would revoke all ingress/egress rules from {sg_id}")

    # Step 5: Delete non-default Security Groups
    for sg in non_default_sgs:
        sg_id = sg['GroupId']
        logging.info(f"Deleting Security Group: {sg_id}")
        if not dry_run:
            try:
                ec2_client.delete_security_group(GroupId=sg_id)
            except ClientError as e:
                logging.error(f"Could not delete Security Group {sg_id}. It may still be in use by a resource: {e}")
        else:
            logging.info(f"[DRY RUN] Would delete Security Group: {sg_id}")

    # Step 6: Delete Network ACLs (non-default)
    for nacl in dependencies['NetworkAcls']:
        if not nacl['IsDefault']:
            nacl_id = nacl['NetworkAclId']
            logging.info(f"Deleting Network ACL: {nacl_id}")
            if not dry_run:
                try:
                    ec2_client.delete_network_acl(NetworkAclId=nacl_id)
                except ClientError as e:
                    logging.error(f"Could not delete Network ACL {nacl_id}: {e}")
            else:
                logging.info(f"[DRY RUN] Would delete Network ACL: {nacl_id}")

    # Step 7: Delete Subnets
    for subnet in dependencies['Subnets']:
        subnet_id = subnet['SubnetId']
        logging.info(f"Deleting Subnet: {subnet_id}")
        if not dry_run:
            try:
                ec2_client.delete_subnet(SubnetId=subnet_id)
            except ClientError as e:
                logging.error(f"Could not delete Subnet {subnet_id}: {e}")
        else:
            logging.info(f"[DRY RUN] Would delete Subnet: {subnet_id}")

    # Step 8: Delete Route Tables (non-main)
    for rt in dependencies['RouteTables']:
        if not any(assoc.get('Main', False) for assoc in rt['Associations']):
            rt_id = rt['RouteTableId']
            logging.info(f"Deleting Route Table: {rt_id}")
            if not dry_run:
                try:
                    ec2_client.delete_route_table(RouteTableId=rt_id)
                except ClientError as e:
                    logging.error(f"Could not delete Route Table {rt_id}: {e}")
            else:
                logging.info(f"[DRY RUN] Would delete Route Table: {rt_id}")

    # Step 9: Finally, the VPC itself
    logging.info(f"Attempting to delete VPC: {vpc_id}")
    if not dry_run:
        try:
            ec2_client.delete_vpc(VpcId=vpc_id)
            logging.info(f"✅ Successfully initiated deletion for VPC '{vpc_id}'.")
        except ClientError as e:
            logging.error(f"❌ Failed to delete VPC '{vpc_id}': {e}")
    else:
        logging.info(f"[DRY RUN] Would delete VPC: {vpc_id}")


def find_vpcs(ec2_client, vpc_id: str = None, name_contains: str = None) -> List[Dict]:
    filters = []
    if vpc_id:
        filters.append({'Name': 'vpc-id', 'Values': [vpc_id]})
    elif name_contains:
        filters.append({'Name': 'tag:Name', 'Values': [f'*{name_contains}*']})
    try:
        response = ec2_client.describe_vpcs(Filters=filters)
        return response.get('Vpcs', [])
    except ClientError as e:
        logging.error(f"An AWS API error occurred while searching for VPCs: {e}")
        return []


def main():
    parser = argparse.ArgumentParser(
        description=(
            "A comprehensive VPC manager to find and delete AWS VPCs "
            "and their dependencies, including Load Balancers and Endpoints."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  Find resources:     python vpc_cleanup.py --name-contains my-app --region us-east-1\n"
            "  Simulate deletion:  python vpc_cleanup.py --vpc-id vpc-0123... --delete --dry-run --region us-east-1\n"
            "  Delete immediately: python vpc_cleanup.py --name-contains test-vpc --delete --region us-east-1"
        ),
    )
    target_group = parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument("--vpc-id", type=str, help="The specific ID of the VPC to target.")
    target_group.add_argument("--name-contains", type=str, help="A string to find VPCs by their 'Name' tag.")
    parser.add_argument("--region", type=str, required=True, help="The AWS region to operate in.")
    parser.add_argument("--delete", action="store_true", help="Enable deletion mode. Without this flag, the script is read-only.")
    parser.add_argument("--dry-run", action="store_true", help="Simulate deletion. Only works if --delete is also specified.")
    args = parser.parse_args()

    # Create Boto3 clients
    ec2_client = boto3.client('ec2', region_name=args.region)
    elbv2_client = boto3.client('elbv2', region_name=args.region)
    elb_client = boto3.client('elb', region_name=args.region)

    vpcs_to_process = find_vpcs(ec2_client, vpc_id=args.vpc_id, name_contains=args.name_contains)
    if not vpcs_to_process:
        logging.warning("No VPCs found matching the criteria. Exiting.")
        return

    logging.info(f"Found {len(vpcs_to_process)} VPC(s) to process.")
    for vpc in vpcs_to_process:
        vpc_id = vpc['VpcId']
        vpc_name = next((tag['Value'] for tag in vpc.get('Tags', []) if tag['Key'] == 'Name'), 'N/A')
        print("\n" + "="*60 + f"\nVPC Name: {vpc_name} | ID: {vpc_id}\n" + "="*60)

        dependencies = get_vpc_dependencies(ec2_client, elbv2_client, elb_client, vpc_id)
        for dep_type, dep_list in dependencies.items():
            print(f"  > Found {len(dep_list)} {dep_type}:")
            if dep_list:
                for item in dep_list:
                    item_id = (item.get(f'{dep_type[:-1]}Id') or
                               item.get('VpcEndpointId') or
                               item.get('GroupId') or
                               item.get('LoadBalancerArn') or
                               item.get('TargetGroupArn') or
                               item.get('LoadBalancerName'))
                    print(f"    - {item_id}")

    if not args.delete:
        logging.info("\nScript ran in 'find-only' mode (read-only).")
        return

    if args.dry_run:
        logging.warning("\n--- DRY RUN MODE ENABLED --- No resources will be deleted.\n")
    else:
        logging.warning("\n--- DELETE MODE ENABLED --- Proceeding with resource deletion.\n")

    for vpc in vpcs_to_process:
        vpc_id = vpc['VpcId']
        dependencies = get_vpc_dependencies(ec2_client, elbv2_client, elb_client, vpc_id)
        delete_vpc_and_dependencies(ec2_client, elbv2_client, elb_client, vpc_id, dependencies, args.dry_run)


if __name__ == '__main__':
    main()
