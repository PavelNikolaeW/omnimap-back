from django.contrib import admin
from .models import Block
from django import forms
from simple_history.admin import SimpleHistoryAdmin


class ChildIDFilter(admin.SimpleListFilter):
    title = 'Фильтр по UUID дочернего блока'
    parameter_name = 'child_id'
    template = 'admin/filters/child_id_filter.html'

    def lookups(self, request, model_admin):
        # Возвращаем фиктивную опцию, чтобы фильтр отобразился.
        return [('all', 'Все')]

    def queryset(self, request, queryset):
        if self.value() and self.value() != 'all':
            return queryset.filter(children__id=self.value())
        return queryset


class BlockAdminForm(forms.ModelForm):
    class Meta:
        model = Block
        fields = '__all__'


class BlockAdmin(SimpleHistoryAdmin):
    list_display = ('id', 'creator', 'updated_at', 'access_type', 'title')
    list_filter = ('access_type', 'creator', 'title', ChildIDFilter)
    search_fields = ('creator__username', 'access_type')
    filter_horizontal = ('visible_to_users', 'editable_by_users', 'children')
    raw_id_fields = ('creator',)
    ordering = ('id',)
    history_list_display = ['changed_by', 'history_date']  # Отображение в истории
    search_fields = ['data']  # Возможность поиска по полям

    fieldsets = (
        ('title', {'fields': ('title',)}),
        (None, {
            'fields': ('creator', 'access_type', 'data',)
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
