from rest_framework import status, serializers, generics, permissions
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from django.contrib.auth import authenticate
from django.utils.translation import gettext_lazy as _
from authentication.models import LoginAttempt
from django.conf import settings
import json
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.core.mail import send_mail
from django.template.loader import render_to_string
from captcha.models import CaptchaStore
import logging

User = get_user_model()

class CaptchaTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Custom token serializer that handles CAPTCHA verification
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['captcha_key'] = serializers.CharField(required=False)
        self.fields['captcha_value'] = serializers.CharField(required=False)
        
    def validate(self, attrs):
        request = self.context.get('request')
        username = attrs.get('username')
        ip_address = self._get_client_ip(request)
        
        # 检查是否需要CAPTCHA
        if LoginAttempt.requires_captcha(username=username, ip_address=ip_address):
            captcha_key = attrs.get('captcha_key')
            captcha_value = attrs.get('captcha_value')
            
            # 如果没有提供验证码
            if not captcha_key or not captcha_value:
                # 记录失败的登录尝试
                LoginAttempt.objects.create(
                    username=username,
                    ip_address=ip_address,
                    successful=False
                )
                raise serializers.ValidationError({
                    'captcha_required': True,
                    'detail': _('CAPTCHA verification required.')
                })
            
            # 验证CAPTCHA
            if not self._verify_captcha(captcha_key, captcha_value):
                # 记录失败的登录尝试
                LoginAttempt.objects.create(
                    username=username,
                    ip_address=ip_address,
                    successful=False
                )
                raise serializers.ValidationError({
                    'captcha_required': True,
                    'detail': _('Invalid CAPTCHA. Please try again.')
                })
        
        # 验证用户
        try:
            data = super().validate(attrs)
            # 记录成功的登录
            LoginAttempt.objects.create(
                username=username,
                ip_address=ip_address,
                successful=True
            )
            return data
        except Exception as e:
            # 记录失败的登录
            LoginAttempt.objects.create(
                username=username,
                ip_address=ip_address,
                successful=False
            )
            raise e
            
    def _get_client_ip(self, request):
        """从请求中获取客户端IP地址"""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR', '0.0.0.0')
        return ip
        
    def _verify_captcha(self, key, value):
        """验证CAPTCHA"""
        try:
            # 检查验证码是否正确
            CaptchaStore.objects.get(hashkey=key, response=value.lower())
            return True
        except CaptchaStore.DoesNotExist:
            return False


class CaptchaTokenObtainPairView(TokenObtainPairView):
    """
    Custom token view that uses the CAPTCHA-enabled serializer
    """
    serializer_class = CaptchaTokenObtainPairSerializer


# Password reset views
class PasswordResetSerializer(serializers.Serializer):
    """
    Serializer for requesting a password reset
    """
    email = serializers.EmailField()
    
    def validate_email(self, value):
        """
        Validate that the email exists
        """
        try:
            user = User.objects.get(email=value)
        except User.DoesNotExist:
            # Don't reveal that the user doesn't exist
            pass
        return value
    
    def save(self):
        """
        Generate a password reset token and send email
        """
        
        email = self.validated_data['email']
        try:
            user = User.objects.get(email=email)
            # Generate token
            token = default_token_generator.make_token(user)
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            
            # Send email
            reset_url = f"{settings.FRONTEND_URL}/auth/reset-password/{uid}/{token}/"
            context = {
                'user': user,
                'reset_url': reset_url,
                'site_name': 'Your Site',
            }
            email_body = render_to_string('password_reset_email.html', context)
            
            send_mail(
                subject="Password Reset Request",
                message=email_body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                html_message=email_body,
                fail_silently=False,
            )
        except User.DoesNotExist:
            # Don't reveal that the user doesn't exist
            pass
        except Exception as e:
            logging.error(f"PasswordResetSerializer.save: {e}")
            raise e


class PasswordResetView(generics.GenericAPIView):
    """
    View for requesting a password reset
    """
    serializer_class = PasswordResetSerializer
    
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            {"detail": "Password reset email has been sent."},
            status=status.HTTP_200_OK
        )


class PasswordResetConfirmSerializer(serializers.Serializer):
    """
    Serializer for confirming a password reset
    """
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(min_length=8, write_only=True)
    
    def validate(self, attrs):
        """
        Validate the token and uid
        """
        try:
            uid = force_str(urlsafe_base64_decode(attrs['uid']))
            user = User.objects.get(pk=uid)
        except (TypeError, ValueError, User.DoesNotExist):
            raise serializers.ValidationError({'uid': ['Invalid user ID']})
        
        if not default_token_generator.check_token(user, attrs['token']):
            raise serializers.ValidationError({'token': ['Invalid or expired token']})
        
        attrs['user'] = user
        return attrs
    
    def save(self):
        """
        Set the new password
        """
        user = self.validated_data['user']
        user.set_password(self.validated_data['new_password'])
        user.save()
        return user


class PasswordResetConfirmView(generics.GenericAPIView):
    """
    View for confirming a password reset
    """
    serializer_class = PasswordResetConfirmSerializer
    
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            {"detail": "Password has been reset successfully."},
            status=status.HTTP_200_OK
        )


class PasswordChangeSerializer(serializers.Serializer):
    """
    Serializer for changing password
    """
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(min_length=8, write_only=True)
    
    def validate_old_password(self, value):
        """
        Validate that the old password is correct
        """
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError('Current password is incorrect')
        return value
    
    def save(self):
        """
        Set the new password
        """
        user = self.context['request'].user
        user.set_password(self.validated_data['new_password'])
        user.save()
        return user


class PasswordChangeView(generics.GenericAPIView):
    """
    View for changing password
    """
    serializer_class = PasswordChangeSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            {"detail": "Password changed successfully."},
            status=status.HTTP_200_OK
        ) 