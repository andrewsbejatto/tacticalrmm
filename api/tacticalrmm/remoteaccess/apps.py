from django.apps import AppConfig


class RemoteAccessConfig(AppConfig):
    name = "remoteaccess"
    default_auto_field = "django.db.models.AutoField"

    def ready(self):
        from django.db.models.signals import pre_delete

        from agents.models import Agent

        from .hooks import on_agent_pre_delete

        # TRMM Desktop goes away with the agent, whatever deletes the agent
        pre_delete.connect(on_agent_pre_delete, sender=Agent, dispatch_uid="remoteaccess_agent_delete")
