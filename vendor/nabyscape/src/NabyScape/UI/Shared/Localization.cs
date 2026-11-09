using System;
using System.Collections.Generic;
using System.Globalization;

namespace NabyScape;

// English source captions are catalog keys. Call only for application-owned UI text.
// Never apply this to transcripts, user labels, filenames, provider IDs or persisted values.
// Shared UI sinks (UI.Text/Button/Heading/Named, Motion.Text, notices, combo-box items) also
// pass their text through Text(): only exact catalog matches change, everything else is shown as-is.
internal static class Localization
{
    internal static readonly string[] LanguageCodes = { "ru", "en", "es", "id" };
    internal static readonly string[] LanguageNames = { "Русский", "English", "Español", "Bahasa Indonesia" };
    internal static string Language { get; private set; } = "en";
    private static CultureInfo? russianCulture;

    // Culture for dates and month/day names shown in the UI. Number parsing keeps CurrentCulture.
    internal static CultureInfo Culture => Language == "ru"
        ? russianCulture ??= CreateRussianCulture() : CultureInfo.CurrentCulture;

    internal static void Configure(Settings settings)
    {
        settings.NormalizeLanguageAccessibility();
        Language = settings.UiLanguage;
        Accessibility.Configure(settings);
        // Deliberately preserve CurrentCulture: numeric editors retain their existing parsing.
    }

    internal static string Text(string english)
    {
        if (string.IsNullOrEmpty(english)) return english;
        if (Language == "ru")
            return LocalizationCatalog.Russian.TryGetValue(english, out var russian) && russian.Length > 0 ? russian : english;
        if (Language == "en" || !LocalizationCatalog.English.TryGetValue(english, out var entry)) return english;
        return Language switch { "es" when entry.Spanish.Length > 0 => entry.Spanish,
            "id" when entry.Indonesian.Length > 0 => entry.Indonesian, _ => english };
    }

    // Display text: exact match first, then a known caption prefix ("Could not save: " + details)
    // or a known caption suffix (reason + " · microphones and screen sharing off…"). Prefixes must
    // end with ": ", " · " or ". " and suffixes start with " · ", ". " or a longer " …" fragment,
    // so ordinary user text (titles, file names, notes) is practically never matched.
    internal static string Message(string english)
    {
        if (string.IsNullOrEmpty(english) || Language == "en") return english;
        string exact = Text(english);
        if (!ReferenceEquals(exact, english) && exact != english) return exact;
        var (prefixes, suffixes) = Fragments();
        foreach (string prefix in prefixes)
            if (english.Length > prefix.Length && english.StartsWith(prefix, StringComparison.Ordinal))
                return Text(prefix) + Message(english.Substring(prefix.Length));
        foreach (string suffix in suffixes)
            if (english.Length > suffix.Length && english.EndsWith(suffix, StringComparison.Ordinal))
                return Message(english.Substring(0, english.Length - suffix.Length)) + Text(suffix);
        return english;
    }

    private static (string[] Prefixes, string[] Suffixes)? fragments;
    private static string? fragmentLanguage;
    private static (string[] Prefixes, string[] Suffixes) Fragments()
    {
        if (fragments is { } cached && fragmentLanguage == Language) return cached;
        IEnumerable<string> keys = Language == "ru" ? LocalizationCatalog.Russian.Keys : LocalizationCatalog.English.Keys;
        var prefixList = new List<string>();
        var suffixList = new List<string>();
        foreach (string key in keys)
        {
            if (key.Length >= 5 && (key.EndsWith(": ", StringComparison.Ordinal) || key.EndsWith(" · ", StringComparison.Ordinal) || key.EndsWith(". ", StringComparison.Ordinal)))
                prefixList.Add(key);
            if (key.Length >= 5 && (key.StartsWith(" · ", StringComparison.Ordinal) || key.StartsWith(". ", StringComparison.Ordinal))
                || key.Length >= 12 && key.StartsWith(" ", StringComparison.Ordinal) && !key.StartsWith("  ", StringComparison.Ordinal))
                suffixList.Add(key);
        }
        prefixList.Sort((a, b) => b.Length.CompareTo(a.Length));
        suffixList.Sort((a, b) => b.Length.CompareTo(a.Length));
        fragmentLanguage = Language;
        var result = (prefixList.ToArray(), suffixList.ToArray());
        fragments = result;
        return result;
    }

    internal static string Format(string english, params object[] values)
        => string.Format(CultureInfo.CurrentCulture, Text(english), values);

    // "{count} {noun}" with the correct Russian plural form. englishOne/englishMany are the
    // English singular and plural nouns; Russian forms come from LocalizationCatalog.RussianPlurals.
    internal static string Count(long count, string englishOne, string englishMany)
    {
        if (Language == "ru" && LocalizationCatalog.RussianPlurals.TryGetValue(englishMany, out var forms))
            return count.ToString(CultureInfo.CurrentCulture) + " " + RussianPlural(count, forms.One, forms.Few, forms.Many);
        return count.ToString(CultureInfo.CurrentCulture) + " " + Text(count == 1 ? englishOne : englishMany);
    }

    internal static string RussianPlural(long count, string one, string few, string many)
    {
        long n = Math.Abs(count) % 100, n1 = n % 10;
        if (n is > 10 and < 20) return many;
        if (n1 == 1) return one;
        return n1 is >= 2 and <= 4 ? few : many;
    }

    private static CultureInfo CreateRussianCulture()
    {
        try { return CultureInfo.GetCultureInfo("ru-RU"); }
        catch (CultureNotFoundException) { return CultureInfo.CurrentCulture; }
    }
}
