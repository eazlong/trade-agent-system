from captcha.models import CaptchaStore
from captcha.helpers import captcha_image_url
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework.viewsets import ViewSet
from .serializers import UserProfileSerializer, UserProfileUpdateSerializer
import logging

class CaptchaAPIView(APIView):
    def get(self, request):
        captcha_key = CaptchaStore.generate_key()
        logging.info(f"CaptchaAPIView.get: {captcha_key}")
        image_url = captcha_image_url(captcha_key)


        return Response({
            'captcha_key': captcha_key,
            'image_url': request.build_absolute_uri(image_url)
        })


class UserProfileViewSet(ViewSet):
    """
    用户个人资料视图集
    """
    permission_classes = [permissions.IsAuthenticated]
    
    def retrieve(self, request):
        """
        获取当前用户的个人资料
        """
        serializer = UserProfileSerializer(request.user)
        return Response(serializer.data)
    
    def update(self, request):
        """
        更新当前用户的个人资料
        """
        serializer = UserProfileUpdateSerializer(request.user, data=request.data, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
