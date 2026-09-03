"""Tier 4 — the screens.

The catalogue is read-only here, because it is generated: an editable
catalogue row would be silently overwritten by the next ``permkit_sync``, and
a UI that lets someone type a change that vanishes on deploy is worse than no
UI. What people compose is on the permission pages, and every reference they
make is a dropdown into the catalogue rather than a string they type.

Django admin has no nested inlines, so ``PermissionRule`` gets its own page
rather than pulling in a dependency for it. The permission page links out to
its rules; the rule page holds its conditions.
"""

from __future__ import annotations

from django import forms
from django.contrib import admin
from django.db.models import Count
from django.shortcuts import render
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

from .catalogue.models import (
    RegisteredEndpoint,
    RegisteredFieldGroup,
    RegisteredFilter,
    RegisteredObject,
    RegisteredScopePoint,
)
from .models import (
    Permission,
    PermissionEndpoint,
    PermissionFieldGrant,
    PermissionRule,
    PermissionRuleCondition,
    Role,
    RolePermission,
)


# -- shared ---------------------------------------------------------------


class ReadOnlyAdmin(admin.ModelAdmin):
    """A generated table. Look, do not touch."""

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


def _liveness(obj) -> str:
    if obj.is_live:
        return format_html('<span style="color:#2e7d32">{}</span>', "live")
    return format_html(
        '<span style="color:#c62828" title="{}">{}</span>',
        "No longer declared in code. Permissions using it still resolve, but "
        "it cannot be edited or reasoned about.",
        "retired",
    )


_liveness.short_description = "state"


# -- tier 1: the catalogue ------------------------------------------------


@admin.register(RegisteredObject)
class RegisteredObjectAdmin(ReadOnlyAdmin):
    list_display = ("key", "label", "model_label", _liveness, "rule_count", "last_seen_at")
    list_filter = ("is_live",)
    search_fields = ("key", "label", "model_label")

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_rules=Count("rules"))

    @admin.display(description="used by", ordering="_rules")
    def rule_count(self, obj) -> int:
        return obj._rules


@admin.register(RegisteredFilter)
class RegisteredFilterAdmin(ReadOnlyAdmin):
    list_display = ("key", "label", "object", "params", "multi_valued", _liveness, "usage")
    list_filter = ("is_live", "multi_valued", "object")
    search_fields = ("key", "label")

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_used=Count("used_by"))

    @admin.display(description="params")
    def params(self, obj) -> str:
        """What an admin must supply to use this filter, from the declaration."""
        if not obj.open_params:
            return "—"
        return ", ".join(
            f"{name} ({spec.get('type')}{'' if spec.get('required') else ', optional'})"
            for name, spec in obj.open_params.items()
        )

    @admin.display(description="used by", ordering="_used")
    def usage(self, obj) -> int:
        return obj._used


@admin.register(RegisteredEndpoint)
class RegisteredEndpointAdmin(ReadOnlyAdmin):
    list_display = ("key", "label", "enforced_by", _liveness, "usage", "last_seen_at")
    list_filter = ("is_live",)
    search_fields = ("key", "label")

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_used=Count("granted_by"))

    @admin.display(description="enforced by")
    def enforced_by(self, obj) -> str:
        return format_html_join(
            mark_safe("<br>"), "{}", ((t,) for t in obj.targets)
        ) or "—"

    @admin.display(description="granted by", ordering="_used")
    def usage(self, obj) -> int:
        return obj._used


@admin.register(RegisteredFieldGroup)
class RegisteredFieldGroupAdmin(ReadOnlyAdmin):
    list_display = ("__str__", "label", "field_list", _liveness, "usage")
    list_filter = ("is_live", "object")

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_used=Count("granted_by"))

    @admin.display(description="fields")
    def field_list(self, obj) -> str:
        return ", ".join(obj.fields or ())

    @admin.display(description="granted by", ordering="_used")
    def usage(self, obj) -> int:
        return obj._used


@admin.register(RegisteredScopePoint)
class RegisteredScopePointAdmin(ReadOnlyAdmin):
    list_display = ("key", "target", _liveness, "last_seen_at")
    list_filter = ("is_live", "object")

    @admin.display(description="key")
    def key(self, obj) -> str:
        return obj.key


# -- tier 2: composition --------------------------------------------------


class PermissionEndpointInline(admin.TabularInline):
    model = PermissionEndpoint
    extra = 1
    autocomplete_fields = ("endpoint",)
    verbose_name_plural = "Endpoints this permission may reach"

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "endpoint":
            kwargs["queryset"] = RegisteredEndpoint.objects.filter(is_live=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class PermissionFieldGrantInline(admin.TabularInline):
    model = PermissionFieldGrant
    extra = 1
    verbose_name_plural = "Fields this permission may see or write"

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "field_group":
            kwargs["queryset"] = RegisteredFieldGroup.objects.filter(
                is_live=True
            ).select_related("object")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ("key", "name", "endpoint_count", "rule_count", "field_count", "held_by")
    search_fields = ("key", "name", "description")
    inlines = [PermissionEndpointInline, PermissionFieldGrantInline]
    readonly_fields = ("rules_summary",)
    fields = ("key", "name", "description", "rules_summary")

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                _endpoints=Count("endpoints", distinct=True),
                _rules=Count("rules", distinct=True),
                _fields=Count("field_grants", distinct=True),
                _roles=Count("role_bindings", distinct=True),
            )
        )

    @admin.display(description="endpoints", ordering="_endpoints")
    def endpoint_count(self, obj) -> int:
        return obj._endpoints

    @admin.display(description="row rules", ordering="_rules")
    def rule_count(self, obj) -> int:
        return obj._rules

    @admin.display(description="field grants", ordering="_fields")
    def field_count(self, obj) -> int:
        return obj._fields

    @admin.display(description="held by roles", ordering="_roles")
    def held_by(self, obj) -> int:
        return obj._roles

    @admin.display(description="Rows this permission may act on")
    def rules_summary(self, obj):
        """Rules link out rather than nesting: admin has no nested inlines.

        Spelling out the union/intersection here is the point — it is the one
        thing about the model an administrator must hold in their head, and
        the place they will be thinking about it is this page.
        """
        if obj.pk is None:
            return "Save the permission first, then add rules."
        rows = format_html_join(
            "",
            '<li><a href="{}">{}</a>{}</li>',
            (
                (
                    reverse("admin:permkit_permissionrule_change", args=[rule.pk]),
                    rule.key,
                    format_html(
                        " — {}", rule.label or "every row (unconditional)"
                    ),
                )
                for rule in obj.rules.select_related("object")
            ),
        )
        add_url = reverse("admin:permkit_permissionrule_add")
        return format_html(
            "<ul>{}</ul>"
            '<p><a class="addlink" href="{}?permission={}">Add a rule</a></p>'
            "<p style='color:#666'>Access is granted when <b>any</b> rule "
            "matches. A rule matches when <b>all</b> of its conditions hold. "
            "A rule with no conditions means every row.</p>",
            rows or mark_safe("<li><i>no rules — this permission grants no rows</i></li>"),
            add_url,
            obj.pk,
        )


class PermissionRuleConditionInline(admin.TabularInline):
    """Conditions AND-ed within the rule.

    The filter dropdown is narrowed to filters that are live *and* declared
    for this rule's object, which is what makes the two silent misconfigurations
    unreachable from the UI rather than merely detected afterwards.
    """

    model = PermissionRuleCondition
    extra = 1

    def get_formset(self, request, obj=None, **kwargs):
        request._permkit_rule = obj
        return super().get_formset(request, obj, **kwargs)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "filter":
            qs = RegisteredFilter.objects.filter(is_live=True).select_related("object")
            rule = getattr(request, "_permkit_rule", None)
            if rule is not None and rule.object_id:
                qs = qs.filter(object_id=rule.object_id)
            kwargs["queryset"] = qs
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(PermissionRule)
class PermissionRuleAdmin(admin.ModelAdmin):
    list_display = ("__str__", "permission", "key", "condition_count", "order")
    list_filter = ("object", "endpoint_key", "permission")
    inlines = [PermissionRuleConditionInline]
    readonly_fields = ("filter_reference",)
    fields = (
        "permission",
        "object",
        "endpoint_key",
        "label",
        "order",
        "filter_reference",
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("permission", "object")
            .annotate(_conditions=Count("conditions"))
        )

    @admin.display(description="key")
    def key(self, obj) -> str:
        return obj.key

    @admin.display(description="conditions", ordering="_conditions")
    def condition_count(self, obj) -> int:
        return obj._conditions

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "object":
            kwargs["queryset"] = RegisteredObject.objects.filter(is_live=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.display(description="Filters available for this object")
    def filter_reference(self, obj):
        """The params each filter expects, on the page where they are typed.

        ``params`` is a JSON field because the schema is per-filter and known
        only at request time; showing the declared schema beside it is what
        stops that being guesswork.
        """
        if obj.pk is None or obj.object_id is None:
            return "Choose an object and save, and its filters will be listed here."
        rows = format_html_join(
            "",
            "<li><b>{}</b> — {}<br><code>{}</code></li>",
            (
                (
                    f.key,
                    f.label,
                    ", ".join(
                        f'"{n}": <{s.get("type")}>' for n, s in f.open_params.items()
                    )
                    or "no params",
                )
                for f in RegisteredFilter.objects.filter(
                    object_id=obj.object_id, is_live=True
                )
            ),
        )
        return format_html("<ul>{}</ul>", rows or mark_safe("<li><i>none</i></li>"))


# -- tier 3: assignment ---------------------------------------------------


class RolePermissionInline(admin.TabularInline):
    model = RolePermission
    extra = 1
    autocomplete_fields = ("permission",)


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("key", "label", "permission_count")
    search_fields = ("key", "label")
    inlines = [RolePermissionInline]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_perms=Count("permissions"))

    @admin.display(description="permissions", ordering="_perms")
    def permission_count(self, obj) -> int:
        return obj._perms


# -- the preview ----------------------------------------------------------


class PreviewForm(forms.Form):
    """Answering "why can this user do X?" without leaving the admin."""

    user = forms.ModelChoiceField(queryset=None, label="Acting as")
    key = forms.ChoiceField(label="Doing")
    object_pk = forms.CharField(
        required=False,
        label="On row (primary key)",
        help_text="Optional. Leave blank to check the endpoint and row scope only.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.contrib.auth import get_user_model
        from .registry import registry

        self.fields["user"].queryset = get_user_model()._default_manager.all()
        self.fields["key"].choices = [
            (k, k) for k in sorted(registry.known_keys())
        ]


def _preview_view(request):
    """Render ``policy.explain()`` for a chosen user, key and row.

    The screen that makes the rest trustworthy. Composing rules is guesswork
    until you can ask the system what it concluded and read back the reason,
    and "why can this user do X?" is the question every access review starts
    with.
    """
    from . import get_policy
    from .registry import registry

    form = PreviewForm(request.GET or None)
    trace = None
    error = None

    if form.is_valid():
        user = form.cleaned_data["user"]
        key = form.cleaned_data["key"]
        obj = None
        pk = form.cleaned_data["object_pk"].strip()
        if pk:
            spec = registry.key(key)
            if spec.model is None:
                error = f"{key} has no model bound, so there is no row to check."
            else:
                obj = spec.model._default_manager.filter(pk=pk).first()
                if obj is None:
                    error = f"No {spec.resource} with primary key {pk!r}."
        if error is None:
            trace = get_policy().explain(user, key, obj)

    return render(
        request,
        "permkit/preview.html",
        {
            **admin.site.each_context(request),
            "title": "Permission preview",
            "form": form,
            "trace": trace,
            "error": error,
        },
    )


def _attach_preview(cls):
    """Hang the preview off the Permission admin's own URLs.

    Not a custom ``AdminSite``: permkit is a pluggable app, and making
    consumers swap their admin site to get one extra page is a bad trade.
    """
    original = cls.get_urls

    def get_urls(self):
        return [
            path(
                "preview/",
                self.admin_site.admin_view(_preview_view),
                name="permkit_preview",
            )
        ] + original(self)

    cls.get_urls = get_urls
    return cls


_attach_preview(PermissionAdmin)
