from django.db import models
from django.utils import timezone
import logging

class LoginAttempt(models.Model):
    """
    Model to track login attempts for implementing CAPTCHA requirements.
    
    Attributes:
        username (CharField): The username attempting to log in.
        ip_address (GenericIPAddressField): The IP address of the login attempt.
        successful (BooleanField): Whether the login attempt was successful.
        timestamp (DateTimeField): When the login attempt occurred.
    """
    username = models.CharField(max_length=150)
    ip_address = models.GenericIPAddressField()
    successful = models.BooleanField(default=False)
    timestamp = models.DateTimeField(default=timezone.now)
    
    class Meta:
        db_table = 'tb_login_attempts'
        app_label = 'authentication'
        
    @classmethod
    def get_recent_attempts(cls, username=None, ip_address=None, minutes=30):
        """
        Get recent login attempts for a username or IP address.
        
        Args:
            username (str, optional): Username to check attempts for.
            ip_address (str, optional): IP address to check attempts for.
            minutes (int, optional): Time window in minutes. Defaults to 30.
            
        Returns:
            QuerySet: Recent login attempts.
        """
        time_threshold = timezone.now() - timezone.timedelta(minutes=minutes)
        filters = {'timestamp__gt': time_threshold}
        
        if username:
            filters['username'] = username
        if ip_address:
            filters['ip_address'] = ip_address
            
        logging.info(f"LoginAttempt.get_recent_attempts: {filters}")
        return cls.objects.filter(**filters)
    
    @classmethod
    def requires_captcha(cls, username=None, ip_address=None):
        """
        Check if a CAPTCHA is required based on recent failed attempts.
        
        Args:
            username (str, optional): Username to check.
            ip_address (str, optional): IP address to check.
            
        Returns:
            bool: True if CAPTCHA is required, False otherwise.
        """
        recent_attempts = cls.get_recent_attempts(username=username, ip_address=ip_address)
        failed_attempts = recent_attempts.filter(successful=False).count()
        return failed_attempts >= 2  # Require CAPTCHA after 2 failed attempts 