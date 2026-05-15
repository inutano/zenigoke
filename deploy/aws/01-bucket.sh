#!/usr/bin/env bash
# Create the S3 bucket for zenigoke catalog data and apply CORS.
# Idempotent: re-running is safe.
#
# Usage:
#   bash deploy/aws/01-bucket.sh [BUCKET_NAME]
#
# Defaults: BUCKET_NAME=zenigoke-catalog  REGION=ap-northeast-1
set -euo pipefail

BUCKET="${1:-${ZENIGOKE_BUCKET:-zenigoke-catalog}}"
REGION="${ZENIGOKE_REGION:-ap-northeast-1}"
CORS_FILE="$(dirname "$0")/../s3-cors.json"

echo "== bucket: $BUCKET (region: $REGION) =="

if aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" 2>/dev/null; then
  echo "  already exists — skip create"
else
  aws s3api create-bucket \
    --bucket "$BUCKET" \
    --region "$REGION" \
    --create-bucket-configuration "LocationConstraint=$REGION"
  echo "  created"
fi

echo "== block public ACLs but allow public-read via bucket policy =="
aws s3api put-public-access-block \
  --bucket "$BUCKET" \
  --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=false,RestrictPublicBuckets=false"

echo "== bucket policy: public-read for GET =="
TMP_POLICY="$(mktemp)"
cat > "$TMP_POLICY" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "PublicReadGetObject",
      "Effect": "Allow",
      "Principal": "*",
      "Action": ["s3:GetObject"],
      "Resource": "arn:aws:s3:::${BUCKET}/*"
    }
  ]
}
EOF
aws s3api put-bucket-policy --bucket "$BUCKET" --policy "file://${TMP_POLICY}"
rm -f "$TMP_POLICY"

echo "== applying CORS (from $CORS_FILE) =="
aws s3api put-bucket-cors \
  --bucket "$BUCKET" \
  --cors-configuration "file://${CORS_FILE}"

echo "== done =="
echo "   BUCKET=$BUCKET"
echo "   REGION=$REGION"
echo "   PUBLIC URL prefix: https://${BUCKET}.s3.${REGION}.amazonaws.com/"
