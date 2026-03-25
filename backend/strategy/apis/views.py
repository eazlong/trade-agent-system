from rest_framework.viewsets import ViewSet
from rest_framework.response import Response
from strategy.apis.serializers import TbStrategyTemplateSerializer
from strategy.models import TbStrategyTemplate

class StrategyTemplateViewSet(ViewSet):
    def list(self, request):
        templates = TbStrategyTemplate.objects.all()
        serializer = TbStrategyTemplateSerializer(templates, many=True)
        return Response(serializer.data)