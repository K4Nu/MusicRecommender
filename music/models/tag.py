from django.db import models

class Tag(models.Model):
    """Normalized, canonical tags"""
    name = models.CharField(max_length=100)
    normalized_name = models.CharField(max_length=100, unique=True, db_index=True)

    category = models.CharField(
        max_length=50,
        choices=[
            ("genre", "Genre"),
            ("mood", "Mood"),
            ("instrument", "Instrument"),
            ("era", "Era"),
            ("style", "Style"),
            ("theme", "Theme"),
            ("other", "Other"),
        ],
        default="other"
    )

    total_usage_count = models.BigIntegerField(default=0)

    is_active=models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-total_usage_count', 'name']
        indexes = [
            models.Index(fields=['normalized_name']),
            models.Index(fields=['category', 'normalized_name']),
            models.Index(fields=['category', '-total_usage_count']),
        ]

    def save(self, *args, **kwargs):
        self.normalized_name = self.normalize(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    @staticmethod
    def normalize(tag_name):
        return tag_name.lower().strip().replace('-', ' ')
