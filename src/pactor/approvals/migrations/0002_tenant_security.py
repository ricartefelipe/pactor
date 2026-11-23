"""Defesas de isolamento no banco: RLS forçada e FKs compostas por organização.

Votos são somente-inserção: um voto registrado não é alterado nem removido.
"""

from django.db import migrations

from pactor.core.db import AddTenantForeignKey, EnableTenantRLS, MakeAppendOnly


class Migration(migrations.Migration):
    dependencies = [("approvals", "0001_initial"), ("contracts", "0003_tenant_security")]

    operations = [
        EnableTenantRLS("workflowdefinition"),
        EnableTenantRLS("approvalround"),
        EnableTenantRLS("approvalstep"),
        EnableTenantRLS("stepapprover"),
        EnableTenantRLS("vote"),
        EnableTenantRLS("delegation"),
        AddTenantForeignKey("approvalround", "contract", to="contracts.Contract"),
        AddTenantForeignKey("approvalround", "revision", to="contracts.Revision"),
        AddTenantForeignKey("approvalround", "workflow", to="approvals.WorkflowDefinition"),
        AddTenantForeignKey("approvalstep", "round", to="approvals.ApprovalRound"),
        AddTenantForeignKey("stepapprover", "step", to="approvals.ApprovalStep"),
        AddTenantForeignKey("vote", "round", to="approvals.ApprovalRound"),
        AddTenantForeignKey("vote", "step", to="approvals.ApprovalStep"),
        AddTenantForeignKey("vote", "delegation", to="approvals.Delegation"),
        AddTenantForeignKey("vote", "revision", to="contracts.Revision"),
        MakeAppendOnly("vote"),
    ]
