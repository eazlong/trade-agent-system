from rest_framework import serializers
from ..models import (
    TbUserBotConfig,
)

from strategy.apis.serializers import (
    TbStrategyTemplateSerializer,
)

class TbUserBotConfigSerializer(serializers.ModelSerializer):
    template = TbStrategyTemplateSerializer(read_only=True)
    class Meta:
        model = TbUserBotConfig
        fields = '__all__'
        # depth = 5


# class ProductSerializer(serializers.ModelSerializer):
#     attributes = ProductAttributeSerializer(read_only=True, many=True)

#     class Meta:
#         model = Product
#         fields = '__all__'
#         depth = 5
