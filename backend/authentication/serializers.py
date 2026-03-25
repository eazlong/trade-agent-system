from rest_framework import serializers
from django.contrib.auth import get_user_model

User = get_user_model()

class UserProfileSerializer(serializers.ModelSerializer):
    """
    用户个人资料序列化器
    """
    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'first_name', 'last_name')
        read_only_fields = ('id', 'username')

class UserProfileUpdateSerializer(serializers.ModelSerializer):
    """
    用户个人资料更新序列化器
    """
    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'email')
        
    def validate_email(self, value):
        """
        验证邮箱是否已被其他用户使用
        """
        user = self.context['request'].user
        if User.objects.exclude(pk=user.pk).filter(email=value).exists():
            raise serializers.ValidationError("此邮箱已被使用")
        return value
        
    def update(self, instance, validated_data):
        """
        更新用户信息
        """
        instance.first_name = validated_data.get('first_name', instance.first_name)
        instance.last_name = validated_data.get('last_name', instance.last_name)
        instance.email = validated_data.get('email', instance.email)
        instance.save()
        return instance 