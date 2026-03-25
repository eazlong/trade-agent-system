from strategy.apis.serializers import TbStrategyTemplateSerializer
from rest_framework import serializers
from notify.models import (
    TbUserNotifyConfig
)
from masters.models import (Attribute, AttributeValue)


class AttributeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Attribute
        fields = '__all__'


class AttributeValueSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttributeValue
        fields = '__all__'

class TbUserNotifyConfigSerializer(serializers.ModelSerializer):
    template = TbStrategyTemplateSerializer(read_only=True)
    
    class Meta:
        model = TbUserNotifyConfig
        fields = '__all__'
        # depth = 5

        


# OLD
# class ProductSerializer(serializers.ModelSerializer):
#     attributes = ProductAttributeSerializer(read_only=True, many=True)

#     class Meta:
#         model = Product
#         fields = '__all__'
#         depth = 5
