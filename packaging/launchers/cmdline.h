#ifndef TSB_CMDLINE_H
#define TSB_CMDLINE_H

#include <wchar.h>

/*
 * Return the part of a Windows command line that follows the program name,
 * unchanged. The program name is skipped by the rule the C runtime uses for
 * argv[0]: a leading double quote runs to the next double quote, anything else
 * runs to the first space or tab. The blanks after it are skipped too. The
 * result points into `cmdline` and is empty when nothing follows.
 */
const wchar_t *tsb_cmdline_rest(const wchar_t *cmdline);

#endif
