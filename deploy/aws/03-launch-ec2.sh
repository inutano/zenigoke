#!/usr/bin/env bash
# Launch (or reuse) an EC2 t3.small for the zenigoke API.
# Idempotent: re-running is safe and prints the existing instance's EIP.
set -euo pipefail

REGION="${ZENIGOKE_REGION:-ap-northeast-1}"
KEY_NAME="${ZENIGOKE_KEY_NAME:-zenigoke}"
SG_NAME="${ZENIGOKE_SG_NAME:-zenigoke-api}"
INST_TAG="${ZENIGOKE_INST_TAG:-zenigoke-api}"
INST_TYPE="${ZENIGOKE_INST_TYPE:-t3.small}"

# Ubuntu 22.04 LTS AMI in ap-northeast-1 (Canonical official; update if EOL)
AMI="${ZENIGOKE_AMI:-ami-0d52744d6551d851e}"

# 1. SSH key pair
if ! aws ec2 describe-key-pairs --region "$REGION" --key-names "$KEY_NAME" >/dev/null 2>&1; then
  echo "Creating key pair $KEY_NAME → ~/.ssh/${KEY_NAME}.pem"
  aws ec2 create-key-pair --region "$REGION" --key-name "$KEY_NAME" \
    --query 'KeyMaterial' --output text > "$HOME/.ssh/${KEY_NAME}.pem"
  chmod 600 "$HOME/.ssh/${KEY_NAME}.pem"
fi

# 2. Security group
SG_ID=$(aws ec2 describe-security-groups --region "$REGION" \
  --filters "Name=group-name,Values=$SG_NAME" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)
if [ -z "$SG_ID" ] || [ "$SG_ID" = "None" ]; then
  echo "Creating security group $SG_NAME"
  SG_ID=$(aws ec2 create-security-group --region "$REGION" \
    --group-name "$SG_NAME" --description "zenigoke API" \
    --query 'GroupId' --output text)
  MY_IP=$(curl -s https://checkip.amazonaws.com)
  aws ec2 authorize-security-group-ingress --region "$REGION" \
    --group-id "$SG_ID" --protocol tcp --port 22 --cidr "${MY_IP}/32"
  aws ec2 authorize-security-group-ingress --region "$REGION" \
    --group-id "$SG_ID" --protocol tcp --port 80 --cidr 0.0.0.0/0
  aws ec2 authorize-security-group-ingress --region "$REGION" \
    --group-id "$SG_ID" --protocol tcp --port 443 --cidr 0.0.0.0/0
fi
echo "Security group: $SG_ID"

# 3. Instance
INST_ID=$(aws ec2 describe-instances --region "$REGION" \
  --filters "Name=tag:Name,Values=$INST_TAG" "Name=instance-state-name,Values=running,pending,stopped" \
  --query 'Reservations[0].Instances[0].InstanceId' --output text 2>/dev/null || true)
if [ -z "$INST_ID" ] || [ "$INST_ID" = "None" ]; then
  echo "Launching $INST_TYPE ($AMI)"
  INST_ID=$(aws ec2 run-instances --region "$REGION" \
    --image-id "$AMI" --instance-type "$INST_TYPE" \
    --key-name "$KEY_NAME" --security-group-ids "$SG_ID" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$INST_TAG}]" \
    --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=20,VolumeType=gp3}' \
    --query 'Instances[0].InstanceId' --output text)
  aws ec2 wait instance-running --region "$REGION" --instance-ids "$INST_ID"
fi
echo "Instance: $INST_ID"

# 4. Elastic IP
EIP=$(aws ec2 describe-addresses --region "$REGION" \
  --filters "Name=tag:Name,Values=$INST_TAG" \
  --query 'Addresses[0].PublicIp' --output text 2>/dev/null || true)
if [ -z "$EIP" ] || [ "$EIP" = "None" ]; then
  ALLOC_ID=$(aws ec2 allocate-address --region "$REGION" --domain vpc \
    --query 'AllocationId' --output text)
  aws ec2 create-tags --region "$REGION" --resources "$ALLOC_ID" \
    --tags "Key=Name,Value=$INST_TAG"
  aws ec2 associate-address --region "$REGION" \
    --instance-id "$INST_ID" --allocation-id "$ALLOC_ID"
  EIP=$(aws ec2 describe-addresses --region "$REGION" \
    --allocation-ids "$ALLOC_ID" --query 'Addresses[0].PublicIp' --output text)
fi

echo ""
echo "=== READY ==="
echo "  Instance ID:  $INST_ID"
echo "  Public IP:    $EIP"
echo "  SSH:          ssh -i ~/.ssh/${KEY_NAME}.pem ubuntu@${EIP}"
echo ""
echo "Next steps:"
echo "  1. In your DNS, point zenigoke.inutano.com → ${EIP} (A record)"
echo "  2. After DNS propagates, SSH in and run:"
echo "     bash deploy/aws/04-ec2-bootstrap.sh"
