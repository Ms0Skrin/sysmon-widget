using System;
using System.ComponentModel;
using System.Globalization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;

namespace NabyScape;

// Safety net for application-owned captions assigned straight to WPF controls
// (window titles, tooltips, expander/menu headers, button and check-box captions, status labels,
// combo-box choices). Only exact catalog matches are replaced, so user content and persisted
// values are untouched: combo-box items keep their English values and only their displayed
// caption is translated. Loaded text blocks and content controls are followed while they are
// in the visual tree, so later status updates such as label.Text="Saved" are translated too.
internal static class LocalizedControls
{
    private static bool installed;
    private static DataTemplate? itemTemplate;
    private static readonly DependencyPropertyDescriptor TextDescriptor =
        DependencyPropertyDescriptor.FromProperty(TextBlock.TextProperty, typeof(TextBlock));
    private static readonly DependencyPropertyDescriptor ContentDescriptor =
        DependencyPropertyDescriptor.FromProperty(ContentControl.ContentProperty, typeof(ContentControl));
    private static readonly DependencyProperty TrackedProperty =
        DependencyProperty.RegisterAttached("LocalizationTracked", typeof(bool), typeof(LocalizedControls), new PropertyMetadata(false));

    internal static void Install()
    {
        if (installed) return;
        installed = true;
        var loaded = new RoutedEventHandler(OnLoaded);
        var unloaded = new RoutedEventHandler(OnUnloaded);
        foreach (var type in new[] { typeof(TextBlock), typeof(ContentControl), typeof(HeaderedItemsControl), typeof(ComboBox), typeof(ListBox) })
            EventManager.RegisterClassHandler(type, FrameworkElement.LoadedEvent, loaded);
        foreach (var type in new[] { typeof(TextBlock), typeof(ContentControl) })
            EventManager.RegisterClassHandler(type, FrameworkElement.UnloadedEvent, unloaded);
        EventManager.RegisterClassHandler(typeof(FrameworkElement), FrameworkElement.ToolTipOpeningEvent, new ToolTipEventHandler(OnToolTipOpening));
    }

    private static void OnLoaded(object sender, RoutedEventArgs e)
    {
        if (Localization.Language == "en") return;
        switch (sender)
        {
            case TextBlock text when !BindingOperations.IsDataBound(text, TextBlock.TextProperty):
                TranslateText(text);
                Track(text, TextDescriptor, OnTextChanged);
                break;
            case Window window:
                Replace(window.Title, value => window.Title = value);
                break;
            case ComboBox or ListBox:
                var items = (ItemsControl)sender;
                if (items.ItemTemplate == null && items.ItemTemplateSelector == null && string.IsNullOrEmpty(items.DisplayMemberPath))
                    items.ItemTemplate = itemTemplate ??= CreateItemTemplate();
                break;
            case HeaderedItemsControl headered when headered.Header is string header:
                Replace(header, value => headered.Header = value);
                break;
            case HeaderedContentControl headered:
                if (headered.Header is string caption) Replace(caption, value => headered.Header = value);
                if (headered.Content is string inner) Replace(inner, value => headered.Content = value);
                break;
            case ContentControl content when ItemsControl.ItemsControlFromItemContainer(content) == null
                && !BindingOperations.IsDataBound(content, ContentControl.ContentProperty):
                TranslateContent(content);
                Track(content, ContentDescriptor, OnContentChanged);
                break;
        }
    }

    private static void OnUnloaded(object sender, RoutedEventArgs e)
    {
        if (sender is not DependencyObject element || !(bool)element.GetValue(TrackedProperty)) return;
        element.SetValue(TrackedProperty, false);
        if (sender is TextBlock) TextDescriptor.RemoveValueChanged(element, OnTextChanged);
        else ContentDescriptor.RemoveValueChanged(element, OnContentChanged);
    }

    private static void Track(DependencyObject element, DependencyPropertyDescriptor descriptor, EventHandler handler)
    {
        if ((bool)element.GetValue(TrackedProperty)) return;
        element.SetValue(TrackedProperty, true);
        descriptor.AddValueChanged(element, handler);
    }

    private static void OnTextChanged(object? sender, EventArgs e)
    {
        if (sender is TextBlock text && !BindingOperations.IsDataBound(text, TextBlock.TextProperty)) TranslateText(text);
    }

    private static void OnContentChanged(object? sender, EventArgs e)
    {
        if (sender is ContentControl content) TranslateContent(content);
    }

    private static void TranslateText(TextBlock text)
    {
        if (text.Inlines.Count <= 1) Replace(text.Text, value => text.Text = value, true);
    }

    private static void TranslateContent(ContentControl content)
    {
        if (content.Content is string caption) Replace(caption, value => content.Content = value, true);
    }

    private static void OnToolTipOpening(object sender, ToolTipEventArgs e)
    {
        if (Localization.Language != "en" && sender is FrameworkElement element && element.ToolTip is string tip)
            Replace(tip, value => element.ToolTip = value, true);
    }

    private static void Replace(string? english, Action<string> apply, bool composite = false)
    {
        if (string.IsNullOrEmpty(english)) return;
        string translated = composite ? Localization.Message(english) : Localization.Text(english);
        if (!ReferenceEquals(translated, english) && translated != english) apply(translated);
    }

    private static DataTemplate CreateItemTemplate()
    {
        var text = new FrameworkElementFactory(typeof(TextBlock));
        text.SetBinding(TextBlock.TextProperty, new Binding { Converter = new CaptionConverter() });
        var template = new DataTemplate { VisualTree = text };
        template.Seal();
        return template;
    }

    private sealed class CaptionConverter : IValueConverter
    {
        public object? Convert(object? value, Type targetType, object? parameter, CultureInfo culture)
            => value is string s ? Localization.Text(s) : value?.ToString();
        public object? ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture)
            => Binding.DoNothing;
    }
}
