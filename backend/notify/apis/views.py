from utils.redis_cache import RedisDict
from strategy.models.strategy import TbStrategyTemplate
from qtcore.notifier import Notify
from qtcore.wss_data_consumer_manager import WSSDataProcessorManager
from rest_framework.viewsets import ViewSet
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from notify.apis.serializers import TbUserNotifyConfigSerializer
from notify.models import TbUserNotifyConfig
import logging

class NotifyConfigViewSet(ViewSet):
    permission_classes = [IsAuthenticated]
    def list(self, request):
        user = request.user
        notify_configs = TbUserNotifyConfig.objects.filter(user_id=user.id)
        for notify_config in notify_configs:
            notify_config.template = TbStrategyTemplate.objects.get(id=notify_config.template_id)
        serializer = TbUserNotifyConfigSerializer(notify_configs, many=True)
        return Response(serializer.data)

    def create(self, request):
        user = request.user
        data=request.data
        logging.info(data)
        data['user_id'] = user.id
        serializer = TbUserNotifyConfigSerializer(data=data)
        if serializer.is_valid():
            serializer.save()
            template = TbStrategyTemplate.objects.get(id=data['template_id'])
            WSSDataProcessorManager().get_consumers(template.name).register(user.id, Notify(user))
            return Response(serializer.data, status=201)
        return Response(serializer.errors, status=400)

    def update(self, request, pk=None):
        user = request.user
        instance = TbUserNotifyConfig.objects.get(id=pk)
        if instance.user_id != user.id:
            return Response({'error': 'Not found'}, status=404)
        
        serializer = TbUserNotifyConfigSerializer(instance, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            template = TbStrategyTemplate.objects.get(id=instance.template_id)
            instance.template = template
            WSSDataProcessorManager().get_consumers(template.name).register(user.id, Notify(user))
            return Response(serializer.data)
        return Response(serializer.errors, status=400)
    
    def destroy(self, request, pk=None):
        user = request.user
        try:
            instance = TbUserNotifyConfig.objects.get(id=pk)
            if instance.user_id != user.id:
                return Response({'error': 'Not found'}, status=404)
            template = TbStrategyTemplate.objects.get(id=instance.template_id)
            instance.template = template
            WSSDataProcessorManager().get_consumers(template.name).unregister(user.id, 'notify')
            instance.delete()
            return Response(status=204)
        except TbUserNotifyConfig.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)
        
class NotifyLogViewSet(ViewSet):
    permission_classes = [IsAuthenticated]
    def list(self, request):
        user = request.user
        
        notify_logs = RedisDict(f'NotifyHistory_{user.id}', {})
        # Convert history object to a serializable format (list of dictionaries)
        serializable_logs = [x for x in notify_logs.values()]
        serializable_logs.sort(key=lambda x: x.time, reverse=True)

        return Response([{ 'message': x.msg, 'time': x.time} for x in serializable_logs])