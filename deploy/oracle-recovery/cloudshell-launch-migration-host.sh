# PASTE THIS INTO ORACLE CLOUD SHELL (one block).
#
# It only does the two things that need Oracle API access: launch a throwaway instance in
# AD-2 (where the clone lives) carrying our recovery public key, and attach the
# kale-ai-recovery-clone boot volume to it. Everything after that — mounting, extracting the
# Kale.ai user database, and shipping it to the new VM — happens from the laptop over SSH.
#
# Safe: touches nothing that is running. The clone is a copy; the live VMs are untouched.
# The instance it creates is deleted again at the end of the migration.

C=$OCI_TENANCY
SUBNET=$(oci network subnet list -c $C --all --query "data[?\"display-name\"=='kale-public'].id | [0]" --raw-output)
IMG=$(oci compute image list -c $C --operating-system "Oracle Linux" --operating-system-version "9" --shape VM.Standard.A2.Flex --sort-by TIMECREATED --query "data[0].id" --raw-output)
CLONE=$(oci bv boot-volume list -c $C --availability-domain cAWG:US-ASHBURN-AD-2 --all --query "data[?\"display-name\"=='kale-ai-recovery-clone'].id | [0]" --raw-output)

printf '#cloud-config\nssh_authorized_keys:\n  - ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEYfzAZJ71K7o7bzbFMmOvR6na+ZpmFrZY1QH4YT6BnE kale-oracle-recovery\n' > /tmp/ci.yaml

INST=$(oci compute instance launch -c $C \
  --availability-domain cAWG:US-ASHBURN-AD-2 \
  --shape VM.Standard.A2.Flex --shape-config '{"ocpus":1,"memoryInGBs":6}' \
  --image-id $IMG --subnet-id $SUBNET --display-name kale-dbmig \
  --assign-public-ip true --user-data-file /tmp/ci.yaml \
  --wait-for-state RUNNING --query 'data.id' --raw-output)

oci compute volume-attachment attach --instance-id "$INST" --type paravirtualized \
  --volume-id "$CLONE" --wait-for-state ATTACHED >/dev/null

IP=$(oci compute instance list-vnics --instance-id "$INST" --query 'data[0]."public-ip"' --raw-output)

echo
echo "=================== GIVE THESE TWO LINES TO CLAUDE ==================="
echo "MIGRATION_HOST=$IP"
echo "MIGRATION_INSTANCE=$INST"
echo "====================================================================="
