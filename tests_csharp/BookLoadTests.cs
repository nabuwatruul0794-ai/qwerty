using System.Text.Json;
using Xunit;
using UglyToad.PdfPig;
using UglyToad.PdfPig.DocumentLayoutAnalysis.TextExtractor;

namespace bottg.Tests;

public class BookLoadTests
{
    private static readonly string BooksDir = Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "books");

    private static List<string> ReadBookLines(string file)
    {
        var lines = new List<string>();
        using var pdf = PdfDocument.Open(file);
        foreach (var page in pdf.GetPages())
        {
            var pageText = ContentOrderTextExtractor.GetText(page);
            foreach (var l in pageText.Split('\n'))
            {
                var t = l.Trim();
                if (t.Length > 0) lines.Add(t);
            }
        }
        return lines;
    }

private static List<string> ReadBookLinesSubprocess(string file)
        {
            var root = Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..");
            var psi = new System.Diagnostics.ProcessStartInfo
            {
                FileName = "dotnet",
                Arguments = $"bottg.dll --read-book \"{file}\"",
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                UseShellExecute = false,
                CreateNoWindow = true,
                WorkingDirectory = root
            };
        using var proc = System.Diagnostics.Process.Start(psi)!;
        proc.WaitForExit();
        var json = proc.StandardOutput.ReadToEnd().Trim();
        if (json == "null") return new List<string>();
        using var doc = JsonDocument.Parse(json);
        return doc.RootElement.GetProperty("lines").EnumerateArray()
            .Select(x => x.GetString()!).Where(s => s.Length > 0).ToList();
    }

    private static List<string> GetBookFiles()
    {
        if (!Directory.Exists(BooksDir)) return new List<string>();
        return Directory.GetFiles(BooksDir, "*.pdf").ToList();
    }

    private static List<string> SortedBookFiles()
    {
        var files = GetBookFiles();
        files.Sort((a, b) =>
        {
            var na = Path.GetFileName(a).ToLowerInvariant();
            var nb = Path.GetFileName(b).ToLowerInvariant();
            int Rank(string n) => n.Contains("fizika") ? 0 : n.Contains("informatika") ? 1
                : n.Contains("algebra") ? 2 : n.Contains("geometriya") ? 3
                : n.Contains("matem") ? 4 : 5;
            int ra = Rank(na), rb = Rank(nb);
            if (ra != rb) return ra - rb;
            return string.Compare(na, nb, StringComparison.OrdinalIgnoreCase);
        });
        return files;
    }

    [Fact]
    public void BooksDirectoryExists()
    {
        Assert.True(Directory.Exists(BooksDir), $"Books directory not found at {BooksDir}");
    }

    [Fact]
    public void BookCountIs22()
    {
        var files = GetBookFiles();
        Assert.Equal(22, files.Count);
    }

    [Fact]
    public void AllFilesArePdf()
    {
        var files = GetBookFiles();
        Assert.All(files, f => Assert.Equal(".pdf", Path.GetExtension(f).ToLowerInvariant()));
    }

    [Fact]
    public void EachBookHasContent()
    {
        var files = GetBookFiles();
        foreach (var file in files)
        {
            var lines = ReadBookLines(file);
            Assert.NotEmpty(lines);
            Assert.True(lines.Count > 100, $"{Path.GetFileName(file)} has only {lines.Count} lines");
        }
    }

    [Fact]
    public void SubprocessAndDirectReadMatch()
    {
        var files = GetBookFiles().Take(3).ToList();
        foreach (var file in files)
        {
            var direct = ReadBookLines(file);
            var sub = ReadBookLinesSubprocess(file);
            Assert.Equal(direct.Count, sub.Count);
            Assert.Equal(direct, sub);
        }
    }

    [Fact]
    public void SortingGroupsBySubject()
    {
        var sorted = SortedBookNames();
        var ranks = sorted.Select(NameToRank).ToList();
        for (int i = 1; i < ranks.Count; i++)
        {
            Assert.True(ranks[i - 1] <= ranks[i],
                $"Sort order violated at index {i}: rank {ranks[i - 1]} before rank {ranks[i]}");
        }
    }

    [Fact]
    public void AllSubjectsPresent()
    {
        var names = GetBookFiles().Select(Path.GetFileName).ToList();
        Assert.Contains(names, n => n.Contains("fizika"));
        Assert.Contains(names, n => n.Contains("informatika"));
        Assert.Contains(names, n => n.Contains("algebra"));
        Assert.Contains(names, n => n.Contains("geometriya"));
        Assert.Contains(names, n => n.Contains("matem"));
    }

    [Fact]
    public void NoDuplicateBooks()
    {
        var names = GetBookFiles().Select(Path.GetFileNameWithoutExtension).ToList();
        var unique = new HashSet<string>(names, StringComparer.OrdinalIgnoreCase);
        Assert.Equal(names.Count, unique.Count);
    }

    [Fact]
    public void MemoryUsageUnderControl()
    {
        var files = GetBookFiles();
        var startMem = GC.GetTotalMemory(true);
        foreach (var file in files)
        {
            ReadBookLines(file);
            GC.Collect();
            GC.WaitForPendingFinalizers();
        }
        var endMem = GC.GetTotalMemory(true);
        Assert.True(endMem - startMem < 500 * 1024 * 1024,
            $"Memory grew by {(endMem - startMem) / 1024 / 1024} MB after reading all books");
    }

    private static List<string> SortedBookNames()
    {
        return SortedBookFiles().Select(Path.GetFileName).ToList();
    }

    private static string NameToSubject(string name)
    {
        var n = name.ToLowerInvariant();
        if (n.Contains("fizika")) return "fizika";
        if (n.Contains("informatika")) return "informatika";
        if (n.Contains("algebra")) return "algebra";
        if (n.Contains("geometriya")) return "geometriya";
        if (n.Contains("matem")) return "matem";
        return "other";
    }

    private static int NameToRank(string name)
    {
        var n = name.ToLowerInvariant();
        if (n.Contains("fizika")) return 0;
        if (n.Contains("informatika")) return 1;
        if (n.Contains("algebra")) return 2;
        if (n.Contains("geometriya")) return 3;
        if (n.Contains("matem")) return 4;
        return 5;
    }
}