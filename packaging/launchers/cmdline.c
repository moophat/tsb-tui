#include "cmdline.h"

const wchar_t *tsb_cmdline_rest(const wchar_t *cmdline)
{
    const wchar_t *p = cmdline;

    if (*p == L'"') {
        p++;
        while (*p != L'\0' && *p != L'"')
            p++;
        if (*p == L'"')
            p++;
    } else {
        while (*p != L'\0' && *p != L' ' && *p != L'\t')
            p++;
    }
    while (*p == L' ' || *p == L'\t')
        p++;
    return p;
}
