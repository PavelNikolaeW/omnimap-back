from audioop import reverse

from django.contrib import admin
from django.urls import path
from django.shortcuts import render
from .models import Block, BlockPermission, BlockLink, BlockUrlLinkModel, Group
from django.utils.html import format_html
from django.contrib import messages

from django.contrib.auth import get_user_model

User = get_user_model()

class BlockPermissionInline(admin.TabularInline):
    """
    Inline-класс для отображения и редактирования BlockPermission внутри BlockAdmin.
    """
    model = BlockPermission
    extra = 1  # Количество дополнительных пустых форм для добавления
    autocomplete_fields = ['user']
    fields = ('user', 'permission')


@admin.register(Block)
class BlockAdmin(admin.ModelAdmin):
    """
    Админ-класс для модели Block, включающий Inline для управления BlockPermission.
    """
    list_display = ('id', 'title', 'parent', 'creator', 'updated_at')
    search_fields = ('title', 'id', 'parent__id', 'creator__username')
    list_filter = ('creator', 'updated_at')
    inlines = [BlockPermissionInline]
    readonly_fields = ('id_with_copy_button', 'updated_at', 'parent_link',
                       'children_links')  # Добавляем поле parent_link в readonly_fields

    def id_with_copy_button(self, obj):
        """
        Отображает ID блока с кнопкой для копирования.
        """
        return format_html(
            '<span id="block-id">{}</span> '
            '<button type="button" onclick="navigator.clipboard.writeText(\'{}\')">Копировать</button>',
            obj.id,
            obj.id
        )

    id_with_copy_button.short_description = "ID с кнопкой копирования"

    def parent_link(self, obj):
        """
        Возвращает ссылку на родительский блок, если он существует.
        """
        if obj.parent:
            # url = reverse('admin:api_block_change', args=[obj.parent.id])
            return format_html('<a href="{}">{}</a>', f'http://localhost:8000/admin/api/block/{obj.parent.id}',
                               obj.parent.title or 'nonTitle')
        return "Нет родителя"

    def children_links(self, obj):
        """
        Возвращает ссылки на дочерние блоки, если они существуют.
        """
        if obj.children.exists():
            links = [
                format_html(
                    '<a href="{}">{}</a>',
                    f'http://localhost:8000/admin/api/block/{child.id}',
                    child.title or 'nonTitle'
                )
                for child in obj.children.all()
            ]
            return format_html('<br>'.join(links))
        return "Нет дочерних блоков"

    parent_link.short_description = "Ссылка на родительский блок"
    children_links.short_description = "Ссылки на дочерние блоки"


@admin.register(BlockPermission)
class BlockPermissionAdmin(admin.ModelAdmin):
    """
    Отдельный админ-класс для модели BlockPermission.
    Позволяет управлять правами доступа отдельно от блоков.
    """
    list_display = ('block', 'user', 'permission')
    list_filter = ('permission', 'block__title')
    search_fields = ('block__title', 'user__username')
    autocomplete_fields = ['block', 'user']

    # Можно добавить readonly_fields или другие настройки по необходимости

    # Если вы хотите предотвратить дублирование записей через админку,
    # можно переопределить метод save_model
    def save_model(self, request, obj, form, change):
        if not change:
            # Проверяем уникальность комбинации (block, user, permission)
            if BlockPermission.objects.filter(
                    block=obj.block,
                    user=obj.user,
                    permission=obj.permission
            ).exists():
                self.message_user(request, "Такая комбинация (блок, пользователь, разрешение) уже существует.",
                                  level=messages.ERROR)
                return
        super().save_model(request, obj, form, change)


@admin.register(BlockLink)
class BlockLinkAdmin(admin.ModelAdmin):
    list_display = ('source', 'target', 'created_at')
    search_fields = ('source__title', 'target__title')


@admin.register(BlockUrlLinkModel)
class BlockUrlLinkModelAdmin(admin.ModelAdmin):
    list_display = ('id', 'source', 'creator', 'slug', 'created_at')
    search_fields = ('source__title', 'creator__username', 'slug')
    list_filter = ('created_at',)
    ordering = ('-created_at',)
    readonly_fields = ('id', 'slug', 'created_at')


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'owner')
    search_fields = ('name', 'owner__username')
    filter_horizontal = ('users',)