using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace KeyenceHelper
{
    /// <summary>
    /// Hand-rolled JSON writer: one object per stdout line, no external
    /// dependency (mirrors the no-build-step ethos of the Python side). Only
    /// covers what this tool ever emits - strings, numbers, bools, null,
    /// and flat IDictionary/IEnumerable values.
    /// </summary>
    internal static class Json
    {
        public static void Line(IDictionary<string, object> obj)
        {
            var sb = new StringBuilder();
            Write(sb, obj);
            Console.Out.Write(sb.ToString());
            Console.Out.Write('\n');
            Console.Out.Flush();
        }

        private static void Write(StringBuilder sb, object value)
        {
            if (value == null) { sb.Append("null"); return; }
            var dict = value as IDictionary<string, object>;
            if (dict != null)
            {
                sb.Append('{');
                bool first = true;
                foreach (var kv in dict)
                {
                    if (!first) sb.Append(',');
                    first = false;
                    WriteString(sb, kv.Key);
                    sb.Append(':');
                    Write(sb, kv.Value);
                }
                sb.Append('}');
                return;
            }
            if (value is string) { WriteString(sb, (string)value); return; }
            if (value is bool) { sb.Append(((bool)value) ? "true" : "false"); return; }
            if (value is int || value is long || value is uint || value is ushort)
            {
                sb.Append(Convert.ToInt64(value).ToString(CultureInfo.InvariantCulture));
                return;
            }
            if (value is double || value is float)
            {
                sb.Append(Convert.ToDouble(value).ToString(CultureInfo.InvariantCulture));
                return;
            }
            var en = value as IEnumerable;
            if (en != null && !(value is string))
            {
                sb.Append('[');
                bool first = true;
                foreach (var item in en)
                {
                    if (!first) sb.Append(',');
                    first = false;
                    Write(sb, item);
                }
                sb.Append(']');
                return;
            }
            WriteString(sb, value.ToString());
        }

        private static void WriteString(StringBuilder sb, string s)
        {
            sb.Append('"');
            foreach (char c in s)
            {
                switch (c)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default:
                        if (c < 0x20)
                            sb.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                        else
                            sb.Append(c);
                        break;
                }
            }
            sb.Append('"');
        }

        /// <summary>Convenience builder so call sites read like object literals.</summary>
        public static Dictionary<string, object> Obj(params object[] kvPairs)
        {
            var d = new Dictionary<string, object>();
            for (int i = 0; i + 1 < kvPairs.Length; i += 2)
                d[(string)kvPairs[i]] = kvPairs[i + 1];
            return d;
        }
    }
}
