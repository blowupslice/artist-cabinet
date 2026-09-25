from django.core.files.storage import FileSystemStorage


class PrivateStorage(FileSystemStorage):
    """Обычное файловое хранилище, но ссылки /files/... ведут на защищённую
    вьюху (cabinet.views.protected_file), которая проверяет права доступа.
    Напрямую веб-сервер эти файлы не отдаёт."""
