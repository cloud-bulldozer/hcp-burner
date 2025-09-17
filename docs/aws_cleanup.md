# 🧹 AWS VPC Cleanup Script

A command-line tool to **find and delete AWS Virtual Private Clouds (VPCs)** and their associated dependencies.  

The script systematically removes components such as **Security Groups, Route Tables, Internet Gateways, NAT Gateways, and Network ACLs** before deleting the VPC itself.  

---

## ⚠️ Warning

This script is **powerful** and can permanently delete your AWS infrastructure.  

- It **does not terminate active resources** like **EC2 instances, RDS databases, or Lambda functions** inside the VPC.  
- If such resources exist, the script will fail.  
- Always run in **`--dry-run` mode first** before enabling deletion.  

---

## 📋 Prerequisites

- **Python 3** (required)  
- **Boto3** (AWS SDK for Python)  

```sh
pip install boto3
```

- **AWS Credentials**: Configure your AWS credentials. The simplest way is with the AWS CLI:  

```sh
aws configure
```

---

## 🚀 Usage

The script supports three modes: **Find-Only (default)**, **Dry Run**, and **Delete**.  

### 1. 🔍 Find Mode (Default)

Lists VPCs and their dependencies that match your criteria without making any changes.  

```sh
python3 vpc_cleanup.py --name-contains <vpc-name-substring> --region <aws-region>
```

**Example:**  

```sh
python3 vpc_cleanup.py --name-contains test-rc-xxxxx-vpc --region us-east-2
```

---

### 2. 🧪 Dry Run Mode (Simulated Deletion)

Simulates deletion by showing what would be removed, without actually deleting anything.  
Highly recommended before running in delete mode.  

```sh
python3 vpc_cleanup.py --name-contains <vpc-name-substring> --region <aws-region> --delete --dry-run
```

---

### 3. 💀 Delete Mode (Permanent Deletion)

Deletes the specified VPC and all its dependencies.  
⚠️ Use with **extreme caution**.  

```sh
python3 vpc_cleanup.py --name-contains <vpc-name-substring> --region <aws-region> --delete
```

---

## 📘 Example Output

**Find Mode Example**  

```
2025-08-19 09:30:41,203 - INFO - Found 1 VPC(s) to process.

============================================================
VPC Name: test-rc-xxxxx-vpc | ID: vpc-0da2a975fxxxxxx
============================================================
  > Found 0 Subnets
  > Found 1 RouteTables: rtb-0309accd7xxxxxx
  > Found 0 InternetGateways
  > Found 0 NatGateways
  > Found 1 NetworkAcls: acl-0de72374cxxxxxx
  > Found 4 SecurityGroups:
    - sg-0e37f2596xxxxxx
    - sg-03234eef0xxxxxx
    - sg-0d04ff57cxxxxxx
    - sg-03f7c8c12xxxxxx

2025-08-19 09:30:43,383 - INFO - Script ran in 'find-only' mode (read-only).
```

**Delete Mode Example**  

```
2025-08-19 09:42:05,557 - WARNING - --- DELETE MODE ENABLED --- Proceeding with resource deletion.
2025-08-19 09:42:08,333 - INFO - --- Processing VPC for deletion: vpc-0da2a975fxxxxxx ---
2025-08-19 09:42:08,333 - INFO - Revoking all rules from Security Group: sg-03234eef0xxxxxx
...
2025-08-19 09:42:14,025 - INFO - ✅ Successfully initiated deletion for VPC 'vpc-0da2a975fxxxxxx'.
```

---

## 🛡️ Best Practices

- Always run with `--dry-run` first.  
