from django.contrib import admin
from .models import Block
from django import forms
from simple_history.admin import SimpleHistoryAdmin


class BlockAdminForm(forms.ModelForm):
    class Meta:
        model = Block
        fields = '__all__'


class BlockAdmin(SimpleHistoryAdmin):
    list_display = ('id', 'creator', 'access_type', 'title')
    list_filter = ('access_type', 'creator', 'title')
    search_fields = ('creator__username', 'access_type')
    filter_horizontal = ('visible_to_users', 'editable_by_users', 'children')
    raw_id_fields = ('creator',)
    ordering = ('id',)
    history_list_display = ['changed_by', 'history_date']  # Отображение в истории
    search_fields = ['data']  # Возможность поиска по полям

    fieldsets = (
        ('title', {'fields': ('title',)}),
        (None, {
            'fields': ('creator', 'access_type', 'data')
        }),
        ('Permissions', {
            'fields': ('visible_to_users', 'editable_by_users')
        }),
        ('Hierarchy', {
            'fields': ('children',)
        }),
    )


admin.site.register(Block, BlockAdmin)

# admin.site.register(Block, BlockAdmin)
