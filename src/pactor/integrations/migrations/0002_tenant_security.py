"""RLS forçada para clientes de integração (a rota global contém apenas identificadores)."""

from django.db import migrations

from pactor.core.db import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("integrations", "0001_initial")]

    operations = [EnableTenantRLS("integrationclient")]
