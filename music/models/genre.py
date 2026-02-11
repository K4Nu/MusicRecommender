from django.db import models

class Genre(models.Model):
    name = models.CharField(max_length=255, unique=True, db_index=True)  # ← Added unique + db_index

    class Meta:
        indexes = [
            models.Index(fields=['name'], name='genre_name_idx'),
        ]

    def __str__(self):
        return self.name