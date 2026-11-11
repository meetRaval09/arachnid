#!/usr/bin/env python3
"""
ARACHNID - BUG HUNTER  (looks only, never changes anything)
===========================================================
    python bugcheck.py [folder]
    python bugcheck.py [folder] --rules        list every rule with an example

The other checks find code that is BROKEN (it does not even run) or USELESS (it does nothing).  This one hunts the
worst kind: code that runs perfectly, shows no error, and quietly does the WRONG thing.

    if answer == "yes" or "y":          always true - "y" on its own counts as true
    items = items.sort()                items is now None - sort() changes the list and returns nothing
    name.strip()                        does nothing - the cleaned text is thrown away
    return                              (in JavaScript, a line break after return returns NOTHING)
        total
    document.getElementById("#menu")    finds nothing - the # belongs to CSS selectors, not ids
    width: 100;                         ignored by the browser - 100 what? px? %?

Languages: Python, JavaScript, TypeScript, HTML, CSS, JSON, Markdown.

Nothing is ever changed.  Every finding is a DECISION for a person (or an IDEA for small polish), because fixing a
logic mistake changes what the program does - only the owner knows what was meant.  Each rule was chosen so that
it is almost never wrong: if a case could reasonably be intended, it is left alone.
"""
import argparse
import ast
import json
import posixpath
import re
import sys
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dedupe  # noqa: E402
import ledger  # noqa: E402

MAX_SIZE = 1_500_000
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec|specs|e2e|cypress|fixtures?|mocks?)(/|$)|(^|/)test_[^/]*$|(^|/)tests?\.[jt]sx?$|"
                       r"_test\.py$|\.(test|spec)\.[jt]sx?$", re.I)

# kind: (pile, title, why it matters, what to do)        pile: "decision" (warning) or "idea" (info)
RULES = {
    # ---------------------------------------------------------------- Python
    "py-or-literal": ("decision", "a condition that is always true",
                      "`x == \"a\" or \"b\"` means `(x == \"a\") or \"b\"`, and \"b\" on its own always counts as true",
                      "write `x == \"a\" or x == \"b\"`, or `x in (\"a\", \"b\")`"),
    "py-none-result": ("decision", "a value that is always None",
                       "sort(), append(), extend(), reverse(), insert() and random.shuffle() change the list in place and give back None",
                       "call it on its own line, or use sorted(...) / a copy if you need the result"),
    "py-no-effect": ("decision", "a line that does nothing",
                     "text is never changed in place: strip(), lower(), replace() ... give back a NEW text, and here it is thrown away",
                     "keep the result, e.g. `name = name.strip()`"),
    "py-missing-self": ("decision", "a method that crashes when it is called",
                        "a function inside a class gets the object as its first value; with no parameters at all, calling it fails",
                        "add `self` as the first parameter, or mark it @staticmethod"),
    "py-method-not-called": ("decision", "a test that is always true",
                             "the method is named but not called (no brackets), and a method itself always counts as true",
                             "add the brackets: `if self.is_valid():`"),
    "py-return-in-init": ("decision", "a crash when the object is created",
                          "__init__ must not return a value - Python raises TypeError",
                          "remove the value from the return, or move the logic out of __init__"),
    "py-return-in-finally": ("decision", "an error that silently disappears",
                             "return / break / continue inside `finally` throws away any error that was on its way out",
                             "move it out of the finally block"),
    "py-unreachable": ("decision", "code that can never run",
                       "it comes right after a return / raise / break / continue in the same block",
                       "remove it, or move it before that line if it was meant to run"),
    "py-missing-f": ("decision", "text that shows {name} literally",
                     "the text has {placeholders} with names from this function, but no f in front, so the braces are printed as they are",
                     "put an f before the quote: f\"...\""),
    "py-len-impossible": ("decision", "a length check that never changes anything",
                          "a length is never below 0, so `len(x) < 0` is always false and `len(x) >= 0` always true",
                          "check what was meant - usually `== 0` or `> 0`"),
    "py-off-by-one": ("decision", "a loop that goes one step too far",
                      "range(len(x) + 1) reaches the position len(x), which does not exist - IndexError on the last step",
                      "use range(len(x))"),
    "py-float-equal": ("decision", "a decimal comparison that can fail",
                       "decimals like 0.1 or 19.99 are stored approximately, so `==` can be false although the numbers look equal",
                       "compare with a small tolerance (math.isclose), or use whole numbers (paise/cents) or Decimal"),
    "py-loop-never-ends": ("decision", "a loop that never ends",
                           "the loop checks a variable that is never changed inside the loop, and nothing inside stops it",
                           "change the variable inside the loop (e.g. `i += 1`), or add a break"),
    "py-input-unchecked": ("decision", "a crash on unexpected input",
                           "int(input()) / float(input()) stops the program with ValueError as soon as someone types a letter",
                           "wrap it in try/except ValueError and ask again"),
    "py-bare-except": ("idea", "errors (and Ctrl+C) silently swallowed",
                       "`except:` with only `pass` hides every error - even the ones you need to know about",
                       "catch the specific error you expect, or at least log it"),
    "py-self-assign": ("idea", "an assignment that does nothing",
                       "a variable is set to itself", "check which value was meant"),
    "py-stale-default": ("decision", "a default value that never updates",
                         "defaults are calculated ONCE, when the function is created - every later call gets that same old time / id / random value",
                         "use `=None` and calculate the value inside the function"),
    "py-exception-not-raised": ("decision", "an error that is created but never raised",
                                "the error object is made and thrown away - the program just continues",
                                "add `raise` in front"),
    "py-modify-while-looping": ("decision", "a loop that skips or repeats items",
                                "the list is changed while the loop walks over it, so items are skipped (remove) or the loop grows (append)",
                                "loop over a copy: `for x in list(items):`, or build a new list"),
    "py-float-index": ("decision", "a crash when this position is used",
                       "`/` always gives a decimal (5/2 = 2.5), and a decimal cannot be a position in a list",
                       "use `//` for whole-number division"),
    "py-find-gt-zero": ("idea", "a search that misses the first position",
                        "find()/index() give 0 when the match is at the very start, and `> 0` treats that as 'not found'",
                        "use `>= 0`, `!= -1`, or `in`"),
    "py-falls-through": ("decision", "a function that sometimes returns nothing",
                         "some paths return a value, but if none of the conditions match the function ends and gives back None",
                         "add a final return (or raise) for the remaining cases"),
    # ---------------------------------------------------------------- JavaScript / TypeScript
    "js-assign-in-condition": ("decision", "a condition that changes a value instead of comparing it",
                               "a single = puts a value in; the condition then only checks that value",
                               "use === to compare (or wrap in extra brackets if the assignment is intended)"),
    "js-nan-compare": ("decision", "a NaN check that never works",
                       "NaN is never equal to anything, not even to NaN", "use Number.isNaN(x)"),
    "js-bad-typeof": ("decision", "a typeof check that is always false",
                      "typeof only ever gives: undefined, object, boolean, number, bigint, string, symbol, function",
                      "fix the spelling of the type name"),
    "js-or-literal": ("decision", "a condition that is always true",
                      "`x === \"a\" || \"b\"` means `(x === \"a\") || \"b\"`, and \"b\" alone always counts as true",
                      "write `x === \"a\" || x === \"b\"`, or `[\"a\", \"b\"].includes(x)`"),
    "js-new-literal-compare": ("decision", "a comparison that is never true",
                               "`[]` and `{}` create a NEW empty list/object, which is never the same object as x",
                               "to test for empty use x.length === 0 or Object.keys(x).length === 0"),
    "js-typo-property": ("decision", "a misspelled built-in name",
                         "JavaScript does not complain about an unknown property - it is simply undefined, or creates a new useless one",
                         "use the correct spelling"),
    "js-length-call": ("decision", "a crash: length is not a function",
                       "`.length` is a number, not something you call", "remove the brackets: `.length`"),
    "js-id-selector": ("decision", "an element lookup that always finds nothing",
                       "getElementById wants the bare id (`menu`), and getElementsByClassName the bare class; # and . belong to CSS selectors",
                       "remove the # / . - or use querySelector(\"#menu\")"),
    "js-on-event": ("decision", "an event listener that never fires",
                    "addEventListener uses event names without 'on' (\"click\", not \"onclick\")", "remove the 'on'"),
    "js-return-newline": ("decision", "a return that gives back nothing",
                          "JavaScript ends the statement at a line break after `return`, so the value on the next line is never returned",
                          "start the value on the same line as return: `return (`"),
    "js-fetch-not-awaited": ("decision", "a response used before it arrives",
                             "fetch() gives back a Promise; without await (or .then) the response is not there yet",
                             "write `await fetch(...)`"),
    "js-foreach-async": ("decision", "async work that is not waited for",
                         "forEach does not wait for async functions - the code after it runs before they finish",
                         "use `for (const x of list) { await ... }` or `await Promise.all(list.map(...))`"),
    "js-async-effect": ("decision", "a React effect that cannot clean up",
                        "useEffect must return nothing or a cleanup function; an async function returns a Promise",
                        "define an async function inside the effect and call it"),
    "js-handler-called": ("decision", "a handler that runs immediately",
                          "with brackets the function runs NOW (while the page is drawn / the line runs), and its result becomes the handler",
                          "pass the function itself, or wrap it: `() => handle(x)`"),
    "js-off-by-one": ("decision", "a loop that goes one step too far",
                      "`i <= list.length` reaches list[list.length], which does not exist (undefined)",
                      "use `i < list.length`"),
    "js-no-effect": ("decision", "a line that does nothing",
                     "text and list methods like trim(), toUpperCase(), replace(), filter(), slice() give back a NEW value, and here it is thrown away",
                     "keep the result, e.g. `name = name.trim()`"),
    "js-plus-equals": ("decision", "`=+` where `+=` was probably meant",
                       "`x =+ 1` sets x to 1; it does not add 1", "write `x += 1` (or `x = +y` if a number conversion is meant)"),
    "js-duplicate-case": ("decision", "a switch case that can never run",
                          "the same case value appears twice; only the first one is ever used", "remove or change the duplicate"),
    "js-index-of-gt-zero": ("idea", "a search that misses the first position",
                            "indexOf() gives 0 when the item is first, and `> 0` treats that as 'not found'",
                            "use `>= 0`, `!== -1`, or includes()"),
    "js-bad-month": ("decision", "a date in the wrong month",
                     "months in new Date() count from 0 (January = 0, December = 11); 12 rolls over to January of the next year",
                     "subtract 1 from the month"),
    # ---------------------------------------------------------------- HTML
    "html-duplicate-id": ("decision", "the same id twice on one page",
                          "an id must be unique; scripts and labels only ever find the first element", "give each element its own id"),
    "html-duplicate-attribute": ("decision", "an attribute written twice on one tag",
                                 "the browser keeps only the first one and silently ignores the other", "keep one"),
    "html-label-target": ("decision", "a label connected to nothing",
                          "<label for=\"x\"> needs an element with id=\"x\"; clicking the label does nothing and screen readers cannot connect them",
                          "make the for and the id match"),
    "html-negative-number": ("decision", "a number field that accepts negative values",
                             "a quantity, price or amount field without min lets people type -5 (the browser accepts it)",
                             "add min=\"0\" (or min=\"1\") - and check it again on the server"),
    "html-button-submits": ("decision", "a button that also submits the form",
                            "inside a <form>, a <button> without type is a SUBMIT button - clicking it reloads/sends the page as well as running onclick",
                            "add type=\"button\" if it should only run its script"),
    "html-bad-input-type": ("decision", "an input with a misspelled type",
                            "an unknown type silently becomes a plain text box, so its checks (email, number, date ...) are lost",
                            "use one of the real input types"),
    "html-bad-script-type": ("decision", "a script that never runs",
                             "a script with an unknown type is ignored by the browser", "use type=\"module\" or leave type out"),
    "html-missing-element": ("decision", "a script looks for an element that does not exist",
                             "no page or component in the project has this id, so the lookup gives null and the next line crashes",
                             "fix the id, or add the element"),
    "html-missing-function": ("decision", "a click that calls a function that does not exist",
                              "no script in the project defines this function, so the click shows an error in the console and does nothing",
                              "fix the name, or add the function"),
    "html-p-closed-early": ("decision", "a block inside a paragraph",
                            "a <p> cannot hold blocks such as <div>, <ul>, <table> or headings: the browser silently ENDS the "
                            "paragraph before the block, so the block is no longer inside it (its paragraph styles are lost) "
                            "and the later </p> becomes an extra empty paragraph",
                            "change the <p> to a <div>, or close the paragraph before the block"),
    "html-link-in-link": ("decision", "a link inside a link",
                          "links cannot be nested: the browser ENDS the first link where the second one starts, so part of "
                          "what you meant to be clickable is not", "close the first link before the second, or use one link"),
    "html-form-in-form": ("decision", "a form inside a form",
                          "the browser IGNORES the inner <form> tag completely: its fields are sent with the OUTER form, "
                          "to the outer form's address", "close the first form before starting the second"),
    "html-button-in-button": ("decision", "a button inside a button",
                              "the browser ENDS the first button where the second one starts, so the page does not have the "
                              "shape you wrote", "close the first button before the second"),
    "html-interactive-inside": ("idea", "something clickable inside a button or link",
                                "a link, button or form field inside a <button> or <a> is not allowed; which one receives the "
                                "click differs between browsers, and screen readers announce it badly",
                                "put them next to each other instead of one inside the other"),
    "html-li-outside-list": ("decision", "a list item without a list",
                             "an <li> must be inside <ul>, <ol> or <menu>; on its own it is laid out wrongly and screen "
                             "readers do not treat it as a list", "wrap the items in <ul> (or <ol>)"),
    "html-self-closing": ("decision", "a tag that cannot close itself",
                          "in HTML the / in <div /> is IGNORED: the element stays open and swallows everything after it "
                          "(only empty elements like <br>, <img>, <input> may be written this way)",
                          "write the closing tag: <div></div>"),
    "html-outside-table-cell": ("decision", "content that the browser moves OUT of the table",
                                "text or elements placed directly inside <table> or <tr> (not inside a <td>/<th>) are "
                                "taken out and shown ABOVE the table - View Source and Inspect disagree",
                                "put it inside a <td> / <th>, or in a <caption>"),
    "html-unclosed-quote": ("decision", "an attribute quote that never closes",
                            "everything up to the next quote mark - including other tags - is swallowed into this "
                            "attribute, so those tags disappear from the page", "add the missing closing quote"),
    "html-stray-character": ("decision", "a stray character shown on the page",
                             "an extra > or quote after a tag is shown to visitors as text (or becomes a broken attribute)",
                             "remove the extra character"),
    "html-bad-entity": ("decision", "a character code the browser does not know",
                        "an unknown &code; is shown literally on the page (for example the text '&nbps;')",
                        "fix the spelling of the code"),
    "html-ambiguous-entity": ("decision", "text that the browser turns into a symbol",
                              "browsers read some codes even without the ; at the end, so '&copy2024' shows as '\u00a92024' "
                              "and '&reg=1' as '\u00ae=1'", "write & as &amp; in this text"),
    "css-table-tr": ("decision", "a style or selector for table rows that never matches",
                     "browsers always put rows inside a <tbody> (even if you did not write one), so `table > tr` finds "
                     "nothing", "use `table tr` or `table > tbody > tr`"),
    "html-boolean-false": ("decision", "an on/off attribute set to \"false\" - it is still ON",
                           "for disabled, checked, hidden, required ... only whether the attribute is THERE counts; "
                           "disabled=\"false\" still disables", "remove the attribute to switch it off"),
    "html-data-case": ("decision", "a data- attribute with capital letters, read from a script",
                       "HTML turns attribute names into lowercase, so data-userId becomes data-userid and the script's "
                       "dataset.userId is undefined", "write it data-user-id (it is then read as dataset.userId)"),
    "html-file-form": ("decision", "a file upload form that sends only the file NAME",
                       "a form with a file field needs method=\"post\" and enctype=\"multipart/form-data\"; without them "
                       "the server receives the name of the file but not the file", "add method=\"post\" enctype=\"multipart/form-data\" to the <form>"),
    "html-field-no-name": ("decision", "a form field whose value is never sent",
                           "when the form is submitted, only fields with a name are sent to the server", "add name=\"...\" to the field"),
    "html-tag-in-head": ("decision", "a visible tag inside <head>",
                         "the browser ENDS <head> at this tag, so the head tags written after it (meta, link, title ...) "
                         "end up in the body, where some of them no longer work", "move the tag into <body>"),
    "html-end-br": ("decision", "</br> adds an extra line break",
                    "there is no closing tag for <br>; browsers read </br> as ANOTHER <br>", "write <br>"),
    "html-nested-quote": ("decision", "a quote inside an attribute that cuts it short",
                          "a \" inside a \"...\" value ends the value there; the rest becomes broken attributes",
                          "write the inner quotes as &quot; or use ' around the value"),
    "text-garbled": ("decision", "garbled characters in the text",
                     "text like \u00e2\u20ac\u2122 is what \u2019 or \u00e9 turn into when a file is saved in one character "
                     "encoding and read in another; visitors see the garbage", "retype the character, and save the file as UTF-8"),
    "html-no-charset": ("idea", "the page does not say its character encoding",
                        "the page has letters outside plain English; without <meta charset=\"utf-8\"> some servers make them "
                        "show as garbage (\u00e2\u20ac\u2122)", "add <meta charset=\"utf-8\"> as the first tag in <head>"),
    "html-list-child": ("idea", "something other than <li> directly inside a list",
                        "only <li> belongs directly inside <ul> / <ol>; screen readers count the items wrongly",
                        "wrap it in an <li>"),
    "html-heading-skip": ("idea", "a heading level is skipped",
                          "headings work like a table of contents (h1 > h2 > h3); jumping from h1 to h4 confuses screen "
                          "readers and search engines", "use the next level, and change the size with CSS"),
    "html-aria-hidden-focus": ("idea", "a hidden-from-screen-readers element that can still be clicked or tabbed to",
                               "aria-hidden=\"true\" hides it from screen readers, but keyboard users still land on it - "
                               "and hear nothing", "remove aria-hidden, or add tabindex=\"-1\""),
    "html-base-tag": ("idea", "a <base> tag changes every relative path on the page",
                      "all links, pictures and scripts written without a leading / are looked up from the <base> address, "
                      "not next to the page", "make sure that is intended; Arachnid checks only the / paths on this page"),
    "html-bad-media": ("decision", "a stylesheet for a screen type that does not exist",
                       "the browser does not know this media type, so the stylesheet (or @media block) NEVER applies",
                       "fix the spelling (screen, print, all ...)"),
    "html-comment-unclosed": ("decision", "a comment that never closes",
                              "a comment ends only at -->; with '-- >' (a space) or no end, everything after it - tags, "
                              "text, buttons - is hidden inside the comment", "write the end as -->"),
    "html-double-escaped": ("decision", "a character code written twice",
                            "&amp;copy; shows the text '&copy;' on the page instead of the symbol", "write &copy; (only one &amp;)"),
    "html-bad-form-method": ("decision", "a form method that does not exist",
                             "an unknown method is silently treated as GET: the form data is put into the address bar "
                             "instead of being posted", "use method=\"post\" or method=\"get\""),
    "html-bad-button-type": ("decision", "a button with a misspelled type",
                             "an unknown type makes the button a SUBMIT button: clicking it sends the form",
                             "use type=\"button\", \"submit\" or \"reset\""),
    "html-min-max": ("decision", "a field whose minimum is bigger than its maximum",
                     "no value can ever pass the check, so the form can never be sent", "swap or fix min and max"),
    "html-radio-name": ("decision", "one group of radio buttons with two different names",
                        "radio buttons only work as a group when the name is EXACTLY the same; with a different capital "
                        "letter they are separate groups, so the visitor can tick both", "use exactly the same name"),
    "html-multi-selected": ("decision", "two options marked selected",
                            "a list that allows one choice keeps only the LAST selected option", "keep selected on one option"),
    "html-form-shadow": ("decision", "a field named like a form command",
                         "a field called name=\"submit\" (or id) replaces form.submit, so form.submit() in the script "
                         "crashes with 'form.submit is not a function'", "rename the field, e.g. name=\"send\""),
    "html-form-missing": ("decision", "a field or button connected to a form that does not exist",
                          "form=\"...\" must be the id of a form on the page; otherwise the button does nothing",
                          "make form=\"...\" match the form's id"),
    "html-missing-anchor": ("decision", "a link to a place on the page that does not exist",
                            "#name jumps to the element with id=\"name\"; there is none, so the click does nothing",
                            "match the link and the id"),
    "html-missing-page": ("decision", "a link to a page that does not exist",
                          "visitors who click it get a 404 (Not found) page", "fix the link, or add the page"),
    "html-no-scheme": ("decision", "a web address without https://",
                       "without https:// the browser treats www.site.com as a FILE on your own site, and shows 404",
                       "write https://www..."),
    "html-local-path": ("decision", "a path on your own computer",
                        "C:\\... and file:/// paths only exist on the computer that wrote the page; everybody else "
                        "(and the live site) gets nothing", "put the file in the project and use a relative path"),
    "html-module-handler": ("decision", "a click calls a function that is hidden inside a module",
                            "functions in <script type=\"module\"> are NOT visible to onclick=\"...\"; the click shows "
                            "'... is not defined' in the console", "attach the click in the module (addEventListener), or "
                            "add window.NAME = NAME"),
    "html-hidden-price": ("idea", "a price sent from a hidden field",
                          "visitors can change hidden fields (DevTools) and pay any price they like - unless the server "
                          "looks the price up again", "send only the product id, and take the price from the server"),
    "js-value-on-text": ("decision", ".value on an element that has no value",
                         "only form fields (input, select, textarea ...) have .value; on a <span>/<div> reading gives "
                         "undefined and writing changes nothing on screen", "use .textContent"),
    "js-checkbox-value": ("decision", "a checkbox tested with .value",
                          "a checkbox's .value is its fixed text ('on'), never empty, so the test is ALWAYS true",
                          "use .checked"),
    "js-value-plus": ("decision", "a number added to a form value - the result is TEXT",
                      "a form field's .value is always text, so + glues them together: 499 + \"1\" gives \"4991\"",
                      "convert first: Number(x.value) or x.valueAsNumber"),
    "js-storage-object": ("decision", "an object saved to storage as '[object Object]'",
                          "localStorage only keeps text; an object or list is saved as the useless text '[object Object]'",
                          "save JSON.stringify(value), read with JSON.parse(...)"),
    "js-json-not-called": ("decision", "a .json (or .text) that is never called",
                           "without () you get the FUNCTION, not the data, so the next step works with nothing",
                           "write r.json()"),
    "css-missing-time-unit": ("decision", "a time without s or ms",
                              "transition / animation times need a unit; `0.3` alone makes the browser ignore the whole line",
                              "write 0.3s"),
    "css-zindex-no-position": ("idea", "z-index that may have no effect",
                               "z-index only works on positioned elements (position: relative/absolute/fixed/sticky) or "
                               "flex/grid children", "add position: relative if it is needed"),
    "css-undefined-var": ("decision", "a CSS variable that is never defined",
                          "var(--name) with no --name anywhere makes the whole line invalid, so the colour, size or space "
                          "silently disappears", "define the variable, fix its spelling, or give a fallback: var(--name, 8px)"),
    "css-media-no-unit": ("decision", "a screen-size rule without a unit",
                          "(max-width: 600) has no unit, so the browser NEVER applies this block", "write 600px"),
    "css-calc-spacing": ("decision", "calc() needs spaces around + and -",
                         "calc(100%-20px) is read as two numbers with nothing between them, so the whole line is ignored",
                         "write calc(100% - 20px)"),
    "css-line-comment": ("decision", "a // comment in CSS",
                         "CSS has no // comments: the browser reads it as part of the next rule and throws that rule away",
                         "use /* ... */"),
    "css-missing-semicolon": ("decision", "a missing ; between two styles",
                              "without the ; both lines run together into one broken value, and BOTH are ignored", "add the ;"),
    "css-late-import": ("decision", "an @import that is ignored",
                        "@import only works at the very top of a style sheet; after any other rule the browser skips it",
                        "move the @import to the top"),
    "css-bad-keyword": ("decision", "a value this style does not accept",
                        "the browser ignores the whole line when the value is not one it knows (DevTools shows a yellow warning)",
                        "fix the spelling of the value"),
    "css-bad-unit": ("decision", "a size with a unit that does not exist",
                     "units must touch the number and be real units (px, rem, %, s ...); otherwise the whole line is ignored",
                     "fix the unit"),
    "html-old-tag": ("idea", "an outdated HTML tag",
                     "this tag was removed from HTML; it still works in browsers today but may stop, and screen readers "
                     "ignore its meaning", "use CSS instead (text-align, font-family, animation ...)"),
    "html-zoom-blocked": ("idea", "zooming is switched off on phones",
                          "user-scalable=no / maximum-scale=1 stops people with poor eyesight from zooming in",
                          "remove user-scalable=no and maximum-scale from the viewport tag"),
    "html-no-doctype": ("idea", "the page has no <!doctype html>",
                        "without it browsers use 'quirks mode', where some sizes and layouts behave differently", "add <!doctype html> as the first line"),
    "html-no-viewport": ("idea", "the page is not set up for phones",
                         "without <meta name=\"viewport\"> phones show a zoomed-out desktop page",
                         "add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"),
    "html-no-lang": ("idea", "the page does not say its language",
                     "screen readers and translators guess the language", "add lang=\"en\" (or your language) to <html>"),
    "html-no-title": ("idea", "the page has no title",
                      "the browser tab and search results show the file name instead", "add <title>...</title> in <head>"),
    "html-img-alt": ("idea", "a picture without a description",
                     "screen readers cannot describe it, and nothing is shown if it fails to load", "add alt=\"...\" (alt=\"\" for decoration)"),
    # ---------------------------------------------------------------- CSS
    "css-unknown-property": ("decision", "a misspelled style property",
                             "the browser silently skips properties it does not know, so this style never applies",
                             "use the correct property name"),
    "css-bad-color": ("decision", "a colour the browser cannot read",
                      "a # colour needs 3, 4, 6 or 8 digits from 0-9 and a-f; anything else is ignored", "fix the colour code"),
    "css-missing-unit": ("decision", "a size without a unit",
                         "apart from 0, sizes need a unit (px, rem, %, ...); `width: 100` is ignored by the browser",
                         "add the unit, e.g. 100px"),
    "css-duplicate-property": ("idea", "the same property twice in one rule",
                               "only the last one counts, so the first value never shows", "keep the one you want"),
    # ---------------------------------------------------------------- JSON
    "json-duplicate-key": ("decision", "the same key twice",
                           "only the LAST value is kept; the other one is silently thrown away", "keep one key"),
    "json-missing-file": ("decision", "package.json points to a file that does not exist",
                          "`npm start` (or importing the package) fails because this file is not there", "fix the path"),
    "json-bad-package-name": ("decision", "a package name npm refuses",
                              "npm package names must be lowercase, without spaces", "use lowercase letters, numbers, - and _"),
    "json-unpinned": ("idea", "a dependency without a version",
                      "\"*\" or \"latest\" installs whatever is newest, so tomorrow's install can break without any change on your side",
                      "use a version range such as ^1.2.3"),
    # ---------------------------------------------------------------- Markdown
    "md-broken-link": ("decision", "a link or picture to a file that does not exist",
                       "readers get a 404 page or a broken picture", "fix the path"),
    "md-broken-anchor": ("decision", "a link to a heading that does not exist",
                         "the link jumps nowhere", "match the heading text (lowercase, spaces become -)"),
    "md-unclosed-fence": ("decision", "a code block that never ends",
                          "everything after the opening ``` is shown as code", "add the closing ```"),
    "md-heading-space": ("decision", "a heading that is shown as plain text",
                         "a heading needs a space after the # (`# Title`, not `#Title`)", "add the space"),
    "md-link-gap": ("decision", "a link that is shown as plain text",
                    "there must be no space between ] and ( in [text](link)", "remove the space"),
    "md-link-space": ("decision", "a link with a space in its address",
                      "a space ends the address, so the link is cut off", "write the space as %20, or rename the file"),
}


def add(out, rel, line, kind, detail):
    out.append({"file": rel, "line": int(line or 0), "kind": kind, "detail": detail})


# =================================================================================================== Python
STR_NO_EFFECT = {"strip", "lstrip", "rstrip", "lower", "upper", "title", "capitalize", "casefold", "swapcase", "zfill",
                 "center", "ljust", "rjust", "removeprefix", "removesuffix", "expandtabs"}
NONE_RESULT = {"sort", "append", "extend", "reverse", "insert"}
STALE_CALLS = re.compile(r"(^|\.)(now|utcnow|today|time|time_ns|uuid1|uuid4|random|randint|token_hex|token_urlsafe|monotonic)$")
EXITS = re.compile(r"(^|\.)(exit|_exit|quit|abort)$")
LOGGERS = {"print", "write", "info", "warning", "warn", "error", "debug", "exception", "critical", "append", "send", "set_text",
           "setText", "insert", "add_row", "title", "set_title", "showinfo", "showerror", "showwarning", "label", "Label"}


def _dotted(f):
    parts = []
    while isinstance(f, ast.Attribute):
        parts.append(f.attr)
        f = f.value
    if isinstance(f, ast.Name):
        parts.append(f.id)
    return ".".join(reversed(parts))


def _terminates(stmts):
    for s in stmts:
        if isinstance(s, (ast.Return, ast.Raise)):
            return True
        if isinstance(s, ast.If) and s.orelse and _terminates(s.body) and _terminates(s.orelse):
            return True
        if isinstance(s, (ast.With, ast.AsyncWith)) and _terminates(s.body):
            return True
        if isinstance(s, ast.Try) and ((_terminates(s.body) and all(_terminates(h.body) for h in s.handlers)
                                        and (not s.orelse or _terminates(s.orelse))) or _terminates(s.finalbody)):
            return True
        if isinstance(s, ast.While) and isinstance(s.test, ast.Constant) and s.test.value and \
                not any(isinstance(n, ast.Break) for n in _walk_same_function(s)):
            return True
        if isinstance(s, ast.Expr) and isinstance(s.value, ast.Call) and EXITS.search(_dotted(s.value.func) or ""):
            return True
        if hasattr(ast, "Match") and isinstance(s, ast.Match):
            if any(isinstance(c.pattern, ast.MatchAs) and c.pattern.pattern is None for c in s.cases) and \
                    all(_terminates(c.body) for c in s.cases):
                return True
    return False


def _walk_same_function(node):
    """Like ast.walk, but does not go into nested functions, classes or lambdas."""
    todo = list(ast.iter_child_nodes(node))
    while todo:
        n = todo.pop()
        yield n
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            todo.extend(ast.iter_child_nodes(n))


def _local_names(fn):
    names = {a.arg for a in fn.args.args + fn.args.kwonlyargs + getattr(fn.args, "posonlyargs", [])}
    for a in (fn.args.vararg, fn.args.kwarg):
        if a:
            names.add(a.arg)
    for n in _walk_same_function(fn):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            names.add(n.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            names |= {(a.asname or a.name).split(".")[0] for a in n.names}
    return names


def _is_nonempty_literal(n):
    return isinstance(n, ast.Constant) and not isinstance(n.value, bool) and n.value not in (None, 0, "", b"") and \
        isinstance(n.value, (str, bytes, int, float))


def check_python(rel, text):
    try:
        return _check_python(rel, text)
    except RecursionError:
        return []


def _check_python(rel, text):
    out = []
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return out
    parent = {}
    for node in ast.walk(tree):
        for ch in ast.iter_child_nodes(node):
            parent[ch] = node
    modules = {(a.asname or a.name).split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | \
              {(a.asname or a.name) for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    kinds_of = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            v = n.value
            if isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) and v.func.attr in NONE_RESULT:
                continue                        # `items = items.sort()` is the very mistake we look for - not evidence
            k = ("list" if isinstance(v, (ast.List, ast.ListComp)) or isinstance(v, ast.Call) and _dotted(v.func) in ("list", "sorted")
                 or isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) and v.func.attr in ("split", "splitlines", "readlines", "keys")
                 else "str" if isinstance(v, (ast.JoinedStr,)) or isinstance(v, ast.Constant) and isinstance(v.value, str)
                 or isinstance(v, ast.Call) and (_dotted(v.func) in ("input", "str") or isinstance(v.func, ast.Attribute) and
                                                 v.func.attr in ("strip", "lower", "upper", "read", "decode", "format", "join", "replace"))
                 else "other")
            for t in n.targets:
                if isinstance(t, ast.Name):
                    kinds_of.setdefault(t.id, set()).add(k)
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for a in n.args.args + n.args.kwonlyargs:
                ann = ast.unparse(a.annotation) if a.annotation else ""
                kinds_of.setdefault(a.arg, set()).add("str" if ann == "str" else "list" if ann.startswith(("list", "List")) else "param")
        elif isinstance(n, (ast.For, ast.comprehension)):
            for t in ast.walk(n.target):
                if isinstance(t, ast.Name):
                    kinds_of.setdefault(t.id, set()).add("other")

    def holds(name, kind):
        k = kinds_of.get(name, set())
        return kind in k and k <= {kind, "param"} if kind == "str" else k == {kind}

    uses_pandas = bool(re.search(r"^\s*(import pandas|from pandas|import polars)", text, re.M))
    module_funcs = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    module_assigned = {t.id for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign))
                       for t in ([n.target] if not isinstance(n, ast.Assign) else n.targets) if isinstance(t, ast.Name)}

    def enclosing_fn(node):
        p = parent.get(node)
        while p is not None and not isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            p = parent.get(p)
        return p

    def in_try_body(node):
        child, p = node, parent.get(node)
        while p is not None and not isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            if isinstance(p, ast.Try) and child in p.body and p.handlers:
                return True
            child, p = p, parent.get(p)
        return False

    tests = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.If, ast.While, ast.IfExp, ast.Assert)):
            tests.add(id(n.test))
        elif isinstance(n, ast.comprehension):
            tests |= {id(c) for c in n.ifs}

    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        # --- always-true "or"
        if id(node) in tests:
            cond = node.operand if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not) else node
            if isinstance(cond, ast.BoolOp) and isinstance(cond.op, ast.Or) and \
                    any(isinstance(v, ast.Compare) and isinstance(v.ops[0], (ast.Eq, ast.NotEq, ast.In)) for v in cond.values):
                lit = [v for v in cond.values if _is_nonempty_literal(v)]
                if lit:
                    add(out, rel, line, "py-or-literal", "`or %s` is always true" % ast.unparse(lit[0])[:40])
            if isinstance(cond, ast.Compare) and len(cond.comparators) == 1 and isinstance(cond.ops[0], (ast.Eq, ast.NotEq)) \
                    and isinstance(cond.comparators[0], ast.BoolOp) and isinstance(cond.comparators[0].op, ast.Or) \
                    and all(isinstance(v, ast.Constant) for v in cond.comparators[0].values):
                add(out, rel, line, "py-or-literal", "`== (%s)` only ever compares with the first value" % ast.unparse(cond.comparators[0])[:40])
            # --- method / function used without brackets
            for v in ([cond] + (cond.values if isinstance(cond, ast.BoolOp) else [])):
                v = v.operand if isinstance(v, ast.UnaryOp) and isinstance(v.op, ast.Not) else v
                if isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name) and v.value.id == "self":
                    cls = parent.get(enclosing_fn(node))
                    if isinstance(cls, ast.ClassDef):
                        for d in cls.body:
                            if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef)) and d.name == v.attr and not d.decorator_list \
                                    and not any((isinstance(t, ast.Attribute) and t.attr == v.attr or isinstance(t, ast.Name) and t.id == v.attr)
                                                and isinstance(t.ctx, ast.Store) for t in ast.walk(cls)):
                                add(out, rel, line, "py-method-not-called", "`self.%s` without ()" % v.attr)
                elif isinstance(v, ast.Name) and v.id in module_funcs and v.id not in module_assigned:
                    fns, fn = [], enclosing_fn(node)
                    while fn is not None:
                        fns.append(fn)
                        fn = enclosing_fn(fn)
                    if not any(v.id in _local_names(f) for f in fns if not isinstance(f, ast.Lambda)):
                        add(out, rel, line, "py-method-not-called", "`%s` is a function; without () it is always true" % v.id)
        if isinstance(node, (ast.Assign, ast.Return)) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute):
            m = node.value.func.attr
            recv = node.value.func.value
            if isinstance(recv, ast.Name) and recv.id in modules:
                pass
            elif m in NONE_RESULT and isinstance(recv, ast.Name) and (holds(recv.id, "list") or (
                    m in ("sort", "append", "extend", "reverse") and not uses_pandas and kinds_of.get(recv.id) <= {"param", "list"}
                    and not (m in ("sort", "reverse") and node.value.args)
                    and not (m == "reverse" and node.value.keywords)
                    and all(k.arg in ("key", "reverse") for k in node.value.keywords)
                    and not re.search(r"(?i)df|frame|table|series|data|arr|tensor|index|matrix|query|qs$", recv.id) and not recv.id[:1].isupper()
                    and isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == recv.id for t in node.targets))):
                add(out, rel, line, "py-none-result", "`%s(...)` gives back None" % ast.unparse(node.value.func)[:50])
        if isinstance(node, (ast.Assign, ast.Return)) and isinstance(node.value, ast.Call) and (_dotted(node.value.func) == "random.shuffle" or
                _dotted(node.value.func) == "shuffle" and re.search(r"^\s*from random import [^\n]*\bshuffle\b", text, re.M)):
            add(out, rel, line, "py-none-result", "`shuffle(...)` gives back None")
        # --- result thrown away
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            c = node.value
            recv = c.func.value if isinstance(c.func, ast.Attribute) else None
            if isinstance(c.func, ast.Attribute) and not c.keywords and (
                    (c.func.attr in STR_NO_EFFECT and (isinstance(recv, ast.Constant) and isinstance(recv.value, str)
                                                       or isinstance(recv, ast.Name) and holds(recv.id, "str")
                                                       or isinstance(recv, ast.Name) and kinds_of.get(recv.id) == {"param"} and
                                                       c.func.attr in ("strip", "lstrip", "rstrip", "lower", "upper", "casefold")))
                    or (c.func.attr == "replace" and len(c.args) >= 2 and isinstance(recv, ast.Name) and holds(recv.id, "str"))) and \
                    not (isinstance(recv, ast.Name) and recv.id in modules):
                add(out, rel, line, "py-no-effect", "`%s(...)` - the result is thrown away" % ast.unparse(c.func)[:50])
            elif isinstance(c.func, ast.Name) and c.func.id in ("sorted", "reversed"):
                add(out, rel, line, "py-no-effect", "`%s(...)` - the result is thrown away" % c.func.id)
            elif isinstance(c.func, ast.Name) and re.fullmatch(r"[A-Z]\w*(Error|Exception)", c.func.id):
                add(out, rel, line, "py-exception-not-raised", "`%s(...)` without raise" % c.func.id)
        # --- methods
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and isinstance(parent.get(node), ast.ClassDef):
            a = node.args
            cls = parent.get(node)
            called_on_self = any(isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute) and x.func.attr == node.name and
                                 isinstance(x.func.value, ast.Name) and x.func.value.id == "self" for x in ast.walk(cls))
            if not node.decorator_list and not a.args and not a.vararg and not getattr(a, "posonlyargs", []) and called_on_self:
                add(out, rel, line, "py-missing-self", "`def %s()` has no self" % node.name)
            if node.name == "__init__":
                for r in _walk_same_function(node):
                    if isinstance(r, ast.Return) and r.value is not None and not (isinstance(r.value, ast.Constant) and r.value.value is None) \
                            and not (isinstance(r.value, ast.Call) and isinstance(r.value.func, ast.Attribute) and r.value.func.attr == "__init__"):
                        add(out, rel, r.lineno, "py-return-in-init", "__init__ returns a value")
        # --- stale defaults
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in node.args.defaults + [x for x in node.args.kw_defaults if x is not None]:
                if isinstance(d, ast.Call) and STALE_CALLS.search(_dotted(d.func) or ""):
                    add(out, rel, d.lineno, "py-stale-default", "default `%s` is calculated only once" % ast.unparse(d)[:50])
            # --- a function that sometimes returns nothing
            rets = [r for r in _walk_same_function(node) if isinstance(r, ast.Return)]
            has_yield = any(isinstance(r, (ast.Yield, ast.YieldFrom)) for r in _walk_same_function(node))
            if rets and not has_yield and node.body and not _terminates(node.body) and \
                    all(r.value is not None and not (isinstance(r.value, ast.Constant) and r.value.value is None) for r in rets):
                last = node.body[-1]
                while isinstance(last, ast.If) and len(last.orelse) == 1 and isinstance(last.orelse[0], ast.If):
                    last = last.orelse[0]                       # follow the elif chain to its end
                chain, cur = [], node.body[-1]
                while isinstance(cur, ast.If):
                    chain.append(cur)
                    cur = cur.orelse[0] if len(cur.orelse) == 1 and isinstance(cur.orelse[0], ast.If) else None
                thresholds = len(chain) >= 2 and all(isinstance(c.test, ast.Compare) and all(isinstance(o, (ast.Lt, ast.LtE, ast.Gt, ast.GtE))
                                                                                         for o in c.test.ops) for c in chain)
                if thresholds and isinstance(last, ast.If) and not last.orelse and all(_terminates(c.body) for c in chain):
                    add(out, rel, line, "py-falls-through", "`%s` returns None when no branch matches" % node.name)
        # --- finally
        if isinstance(node, ast.Try) and node.finalbody:
            for s in node.finalbody:
                for n in [s] + list(_walk_same_function(s)):
                    if isinstance(n, (ast.Return, ast.Break, ast.Continue)):
                        if isinstance(n, (ast.Break, ast.Continue)) and any(isinstance(p, (ast.For, ast.While)) for p in _walk_same_function(s)):
                            continue            # belongs to a loop inside the finally block
                        add(out, rel, n.lineno, "py-return-in-finally", "`%s` inside finally" % type(n).__name__.lower())
        # --- unreachable
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(node, field, None)
            if isinstance(stmts, list) and stmts and isinstance(stmts[0], ast.stmt):
                for i, s in enumerate(stmts[:-1]):
                    if isinstance(s, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
                        nxt = stmts[i + 1]
                        if not (isinstance(nxt, ast.Expr) and isinstance(nxt.value, (ast.Constant, ast.Yield, ast.YieldFrom, ast.Await))):
                            add(out, rel, nxt.lineno, "py-unreachable", "after `%s` on line %d" % (type(s).__name__.lower(), s.lineno))
                        break
        # --- missing f
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "{" in node.value and not isinstance(parent.get(node), ast.JoinedStr):
            fn = enclosing_fn(node)
            p = parent.get(node)
            ph = re.findall(r"\{([A-Za-z_]\w*)(?:\.\w+|\[[^\]]*\])?(?:![rsa])?(?::[^{}]*)?\}", node.value)
            if fn is not None and ph and not re.search(r"\{\{|\}\}", node.value) and isinstance(p, ast.Call) and node in p.args \
                    and not p.keywords and not any(isinstance(x, ast.Dict) for x in p.args):   # template APIs fill {x} from a dict
                fname = p.func.attr if isinstance(p.func, ast.Attribute) else (p.func.id if isinstance(p.func, ast.Name) else "")
                if fname in LOGGERS or (isinstance(parent.get(p), ast.Raise)):
                    if all(x in _local_names(fn) for x in ph) and not (isinstance(p.func, ast.Attribute) and p.func.attr in ("format", "format_map")):
                        add(out, rel, node.lineno, "py-missing-f", "{%s} is printed as it is" % ph[0])
        # --- comparisons
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            l, op, r = node.left, node.ops[0], node.comparators[0]
            is_len = lambda x: isinstance(x, ast.Call) and isinstance(x.func, ast.Name) and x.func.id == "len"
            zero = lambda x: isinstance(x, ast.Constant) and x.value == 0 and not isinstance(x.value, bool)
            if (is_len(l) and zero(r) and isinstance(op, (ast.Lt, ast.GtE))) or (zero(l) and is_len(r) and isinstance(op, (ast.Gt, ast.LtE))):
                add(out, rel, line, "py-len-impossible", "`%s`" % ast.unparse(node)[:50])
            fl = [x for x in (l, r) if isinstance(x, ast.Constant) and isinstance(x.value, float)]
            if isinstance(op, (ast.Eq, ast.NotEq)) and fl and not float(fl[0].value * 64).is_integer():
                add(out, rel, line, "py-float-equal", "`%s`" % ast.unparse(node)[:50])
            if isinstance(op, ast.Gt) and zero(r) and isinstance(l, ast.Call) and isinstance(l.func, ast.Attribute) and l.func.attr in ("find", "index", "rfind"):
                add(out, rel, line, "py-find-gt-zero", "`%s`" % ast.unparse(node)[:50])
        # --- loops
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name) and isinstance(node.iter, ast.Call) and _dotted(node.iter.func) == "range":
            a = node.iter.args
            end = a[-1] if len(a) in (1, 2) else None
            if isinstance(end, ast.BinOp) and isinstance(end.op, ast.Add) and isinstance(end.right, ast.Constant) and end.right.value == 1 \
                    and isinstance(end.left, ast.Call) and _dotted(end.left.func) == "len" and end.left.args and isinstance(end.left.args[0], ast.Name):
                seq, i = end.left.args[0].id, node.target.id
                if any(isinstance(s, ast.Subscript) and isinstance(s.value, ast.Name) and s.value.id == seq and isinstance(s.slice, ast.Name)
                       and s.slice.id == i for s in ast.walk(node)):
                    add(out, rel, line, "py-off-by-one", "range(len(%s) + 1) with %s[%s]" % (seq, seq, i))
        if isinstance(node, ast.For) and isinstance(node.iter, ast.Name) and not any(
                isinstance(x, ast.Name) and x.id == node.iter.id and isinstance(x.ctx, ast.Store) for x in _walk_same_function(node)):
            seq, target = node.iter.id, node.target.id if isinstance(node.target, ast.Name) else None
            loop_nodes = list(_walk_same_function(node))
            leaves = any(isinstance(x, (ast.Break, ast.Return, ast.Raise)) for x in loop_nodes)
            for c in loop_nodes:
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and isinstance(c.func.value, ast.Name) and c.func.value.id == seq:
                    if c.func.attr == "remove" and target and len(c.args) == 1 and isinstance(c.args[0], ast.Name) and c.args[0].id == target and not leaves:
                        add(out, rel, c.lineno, "py-modify-while-looping", "`%s.remove(%s)` while looping over %s - the next item is skipped" % (seq, target, seq))
                        break
        if isinstance(node, ast.While) and isinstance(node.test, ast.Compare) and \
                all(isinstance(o, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)) for o in node.test.ops):
            parts = [node.test.left] + node.test.comparators
            simple = all(isinstance(x, (ast.Name, ast.Constant)) or (isinstance(x, ast.Call) and _dotted(x.func) == "len"
                         and len(x.args) == 1 and isinstance(x.args[0], ast.Name)) for x in parts)
            names = {x.id for x in parts if isinstance(x, ast.Name)}
            lens = {x.args[0].id for x in parts if isinstance(x, ast.Call) and x.args and isinstance(x.args[0], ast.Name)}
            body_nodes = [n for s in node.body + node.orelse for n in [s] + list(_walk_same_function(s))]
            changed = {n.id for n in body_nodes if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del))}
            touched = {n.value.id for n in body_nodes if isinstance(n, (ast.Attribute, ast.Subscript)) and isinstance(n.value, ast.Name)} | \
                      {a.id for n in body_nodes if isinstance(n, ast.Call) for a in n.args if isinstance(a, ast.Name)}
            stops = any(isinstance(n, (ast.Break, ast.Return, ast.Raise, ast.Yield, ast.YieldFrom, ast.Global, ast.Nonlocal, ast.Await))
                        or isinstance(n, ast.Call) and EXITS.search(_dotted(n.func) or "") for n in body_nodes)
            unknown_calls = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id not in ("print", "len", "str", "int", "float")
                                for n in body_nodes)
            if simple and (names or lens) and not ((names | lens) & changed) and not (lens & touched) and not stops \
                    and not (lens and unknown_calls) \
                    and any(not isinstance(x, ast.Constant) for x in parts) and (names - {x.id for x in parts[1:] if isinstance(x, ast.Name)} or lens or names):
                left = parts[0]
                if isinstance(left, (ast.Name, ast.Call)):
                    add(out, rel, line, "py-loop-never-ends", "`while %s` - nothing inside the loop changes it" % ast.unparse(node.test)[:40])
        # --- input validation
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("int", "float") and node.args and \
                isinstance(node.args[0], ast.Call) and _dotted(node.args[0].func) == "input" and not in_try_body(node):
            add(out, rel, line, "py-input-unchecked", "`%s(input(...))` without try" % node.func.id)
        # --- bare except: pass
        if isinstance(node, ast.ExceptHandler) and node.type is None and all(isinstance(s, ast.Pass) or
                                                                            isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) for s in node.body):
            add(out, rel, node.lineno, "py-bare-except", "`except: pass`")
        # --- x = x
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and \
                isinstance(node.value, ast.Name) and node.targets[0].id == node.value.id and enclosing_fn(node) is not None:
            add(out, rel, line, "py-self-assign", "`%s = %s`" % (node.value.id, node.value.id))
        # --- list[a / b]
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.BinOp) and isinstance(node.slice.op, ast.Div) and not uses_pandas:
            if not isinstance(node.value, ast.Name) or not re.search(r"(?i)(df|frame|data|path|dir|root)", node.value.id):
                add(out, rel, line, "py-float-index", "`%s`" % ast.unparse(node)[:50])
    return out


# =================================================================================================== JavaScript / TypeScript
REGEX_BEFORE = set("(,=:[!&|?{};+-*%~^")
REGEX_WORDS = {"return", "typeof", "case", "in", "of", "new", "delete", "void", "throw", "else", "do", "await", "yield", "instanceof"}


def js_lex(text):
    """Returns (code, strings): code has comments blanked and the INSIDE of strings / templates / regexes blanked
    (quotes kept, newlines kept, same length), so patterns only ever see real code.  strings = [(start, end)] of
    every quoted text's inside, to read the real text back when a rule needs it."""
    out, strings, i, n = list(text), [], 0, len(text)
    last = ""                                   # last significant code character / word before the current position

    def blank(a, b):
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "
    while i < n:
        c = text[i]
        nx = text[i + 1] if i + 1 < n else ""
        if c == "/" and nx == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
            continue
        if c == "/" and nx == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            blank(i, j)
            i = j
            continue
        if c in "'\"`":
            j = i + 1
            while j < n and text[j] != c:
                if text[j] == "\\":
                    j += 1
                elif c != "`" and text[j] == "\n":
                    break
                j += 1
            strings.append((i + 1, min(j, n)))
            blank(i + 1, min(j, n))
            i = j + 1
            last = "a"
            continue
        if c == "/" and (last == "" or last in REGEX_BEFORE or last in REGEX_WORDS):
            j, cls = i + 1, False
            while j < n and text[j] != "\n":
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "[":
                    cls = True
                elif text[j] == "]":
                    cls = False
                elif text[j] == "/" and not cls:
                    break
                j += 1
            if j < n and text[j] == "/":
                blank(i + 1, j)
                i = j + 1
                while i < n and text[i].isalpha():
                    i += 1
                last = "a"
                continue
        if c.isalnum() or c in "_$":
            j = i
            while j < n and (text[j].isalnum() or text[j] in "_$"):
                j += 1
            last = text[i:j]
            i = j
            continue
        if not c.isspace():
            last = c
        i += 1
    return "".join(out), strings


def _match_close(code, i):
    """code[i] is ( [ or { - index of the matching closer (or len)."""
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack = []
    for j in range(i, len(code)):
        ch = code[j]
        if ch in pairs:
            stack.append(pairs[ch])
        elif ch in ")]}":
            if not stack or stack.pop() != ch:
                return len(code)
            if not stack:
                return j
    return len(code)


def _split_top(expr, sep):
    """Split expr on sep (e.g. '||') at bracket depth 0 -> [(offset, part)]."""
    parts, depth, start, i = [], 0, 0, 0
    while i < len(expr):
        ch = expr[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0 and expr.startswith(sep, i):
            parts.append((start, expr[start:i]))
            i += len(sep)
            start = i
            continue
        i += 1
    parts.append((start, expr[start:]))
    return parts


JS_TYPEOF = {"unknown", "undefined", "object", "boolean", "number", "bigint", "string", "symbol", "function"}
JS_TYPOS = {"lenght": "length", "lengh": "length", "legnth": "length", "lenth": "length", "innerHtml": "innerHTML",
            "innerHtmL": "innerHTML", "textcontent": "textContent", "innertext": "innerText", "getElementByID": "getElementById",
            "getElementbyId": "getElementById", "getElementsByClass": "getElementsByClassName", "querySelectorall": "querySelectorAll",
            "queryselector": "querySelector", "addEventlistener": "addEventListener", "addeventlistener": "addEventListener",
            "preventdefault": "preventDefault", "stopPropogation": "stopPropagation", "toLowercase": "toLowerCase",
            "toUppercase": "toUpperCase", "tolowercase": "toLowerCase", "touppercase": "toUpperCase", "tostring": "toString",
            "indexof": "indexOf", "foreach": "forEach", "stringfy": "stringify", "Stringify": "stringify", "Parse": "parse",
            "appendchild": "appendChild", "removechild": "removeChild", "createelement": "createElement", "setattribute": "setAttribute",
            "getattribute": "getAttribute", "classlist": "classList", "onclik": "onclick", "subString": "substring", "charat": "charAt",
            "tofixed": "toFixed", "parseint": "parseInt", "parsefloat": "parseFloat", "Round": "round", "Floor": "floor", "Ceil": "ceil",
            "Random": "random", "Log": "log"}
NO_EFFECT_JS = {"trim", "trimStart", "trimEnd", "toUpperCase", "toLowerCase", "toLocaleUpperCase", "toLocaleLowerCase",
                "replace", "replaceAll", "slice", "concat", "filter", "substring", "substr", "padStart", "padEnd",
                "repeat", "toFixed", "split", "flat", "toSorted", "toReversed"}
HANDLER_FACTORY = re.compile(r"(bind|debounce|throttle|memo|Handler|^make|^create|^get|^use|^curry|^on[A-Z]|Callback)", re.I)


def _line(text, off):
    return text.count("\n", 0, off) + 1


def check_js(rel, text):
    try:
        return _check_js(rel, text)
    except RecursionError:
        return []


def _check_js(rel, text):
    out = []
    lines_ = text.split("\n")
    if max((len(x) for x in lines_), default=0) > 2000 or len(text) / max(1, len(lines_)) > 200:
        return out                                  # minified / bundled file - not written by a person
    if re.search(r"(?i)(^|/)(jquery|bootstrap|materialize|three|lodash|underscore|d3|chart|moment|popper|highlight|prism|codemirror|"
                 r"leaflet|swiper|select2|datatables|socket\.io)([.-][\w.-]*)?\.js$", rel) or "/vendor/" in "/" + rel or \
            (len(text) > 15000 and re.search(r"/\*!|@license|Copyright \(c\)|MIT License", text[:800])):
        return out                                  # someone else's bundled library
    code, strings = js_lex(text)
    is_jsx = rel.lower().endswith((".jsx", ".tsx")) or bool(re.search(r"<[A-Z][\w.]*[\s/>]|</\w+>", code))

    def string_at(pos):
        """Text inside the quote that starts at code[pos]."""
        for a, b in strings:
            if a == pos + 1:
                return text[a:b]
        return None

    # --- conditions: if (...) / while (...)
    for m in re.finditer(r"\b(if|while)\s*\(", code):
        start = m.end() - 1
        end = _match_close(code, start)
        cond = code[start + 1:end]
        line = _line(text, m.start())
        inner = cond.strip()
        if not (inner.startswith("(") and _match_close(inner, 0) == len(inner) - 1) and len(_split_top(cond, ",")) == 1:
            # ((x = y)) means "I know"; (a = 1, b = 2, ...) is generated code
            depth, k = 0, 0
            while k < len(cond):
                ch = cond[k]
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    depth -= 1
                elif ch == "=" and depth == 0:
                    prev, nxt = cond[k - 1] if k else "", cond[k + 1] if k + 1 < len(cond) else ""
                    rhs = re.split(r"&&|\|\||\)|,|;", cond[k + 1:])[0].strip()
                    plain = re.fullmatch(r"-?\d[\d.]*|(['\"])\s*\1|true|false|null|undefined", rhs) or \
                        (m.group(1) == "if" and re.fullmatch(r"[A-Za-z_$][\w$]*", rhs) and rhs not in ("this", "arguments"))
                    if prev not in "=!<>+-*/%&|^?" and nxt not in "=>" and plain:
                        add(out, rel, line, "js-assign-in-condition", "`%s`" % " ".join(text[start + 1:end].split())[:60])
                        break
                    if nxt == "=":
                        k += 2 if k + 2 < len(cond) and cond[k + 2] == "=" else 1
                k += 1
        ors = _split_top(cond, "||")
        if len(ors) > 1 and any(re.search(r"[=!]==?", p) for _, p in ors):
            for off, p in ors:
                ps = p.strip()
                q = re.fullmatch(r"(['\"])\s*\1", ps)              # a quoted text (its inside is blanked)
                if q:
                    real = string_at(start + 1 + off + p.index(ps[0]))
                    if real:
                        add(out, rel, line, "js-or-literal", "`|| \"%s\"` is always true" % real[:30])
                        break
                elif re.fullmatch(r"-?[1-9]\d*(\.\d+)?", ps):
                    add(out, rel, line, "js-or-literal", "`|| %s` is always true" % ps)
                    break
    # --- comparisons
    for m in re.finditer(r"[=!]==?\s*NaN\b|\bNaN\s*[=!]==?", code):
        add(out, rel, _line(text, m.start()), "js-nan-compare", "`%s`" % m.group(0))
    for m in re.finditer(r"\btypeof\s+[\w$.\[\]?]+\s*[=!]==?\s*(['\"])", code):
        s = string_at(m.end() - 1)
        if s is not None and s not in JS_TYPEOF:
            add(out, rel, _line(text, m.start()), "js-bad-typeof", "typeof ... === \"%s\"" % s[:20])
    for m in re.finditer(r"(['\"])\s*\1\s*[=!]==?\s*typeof\b", code):
        s = string_at(m.start())
        if s is not None and s not in JS_TYPEOF:
            add(out, rel, _line(text, m.start()), "js-bad-typeof", "\"%s\" === typeof ..." % s[:20])
    for m in re.finditer(r"[=!]==?\s*(\[\s*\]|\{\s*\})(?!\s*[.\[])", code):
        add(out, rel, _line(text, m.start()), "js-new-literal-compare", "`%s`" % m.group(0))
    for m in re.finditer(r"\.(indexOf|search|findIndex)\s*\(", code):
        end = _match_close(code, m.end() - 1)
        if re.match(r"\s*>\s*0(?![\d.])", code[end + 1:end + 8]):
            add(out, rel, _line(text, m.start()), "js-index-of-gt-zero", "`.%s(...) > 0`" % m.group(1))
    # --- typos in built-in names
    for m in re.finditer(r"\.\s*([A-Za-z]+)\b", code):
        w = m.group(1)
        if w in JS_TYPOS and len(re.findall(r"\.\s*%s\b" % re.escape(w), code)) < 3 and not re.match(r"\s*:", code[m.end():m.end() + 3]) and \
                not re.search(r"(this\.%s\s*=|\b%s\s*:|\b(get|set)\s+%s\b|^\s*%s\s*[=(;]|\b%s\s*\([^)]*\)\s*\{)" % ((re.escape(w),) * 5), code, re.M):
            if w in ("Parse", "Stringify", "stringfy") and not code[max(0, m.start() - 4):m.start()] == "JSON":
                continue
            if w in ("Round", "Floor", "Ceil", "Random") and not code[max(0, m.start() - 4):m.start()] == "Math":
                continue
            if w == "Log" and not code[max(0, m.start() - 7):m.start()] == "console":
                continue
            ln = text.split("\n")[_line(text, m.start()) - 1]
            if JS_TYPOS[w] in ln:
                continue                            # both spellings on purpose: cfg.innerHtml || cfg.innerHTML
            add(out, rel, _line(text, m.start()), "js-typo-property", "`.%s` should be `.%s`" % (w, JS_TYPOS[w]))
    for m in re.finditer(r"(['\"]\s*['\"]|\]|\b(?:\w*(?:list|items|arr|array|names|text|str|string|word|words|rows|values|keys|chars))\b)\s*\.length\s*\(", code, re.I):
        add(out, rel, _line(text, m.start()), "js-length-call", "`.length()`")
    # --- DOM lookups and events
    for m in re.finditer(r"\b(getElementById|getElementsByClassName|getElementsByTagName)\s*\(\s*(['\"])", code):
        s = string_at(m.end() - 1)
        if s and (m.group(1) == "getElementById" and s[:1] in "#." or m.group(1) == "getElementsByClassName" and s[:1] in ".#"):
            add(out, rel, _line(text, m.start()), "js-id-selector", "%s(\"%s\")" % (m.group(1), s[:30]))
    for m in re.finditer(r"\baddEventListener\s*\(\s*(['\"])", code):
        s = string_at(m.end() - 1)
        if s and re.fullmatch(r"on[a-z]+", s) and s not in ("online", "one"):
            add(out, rel, _line(text, m.start()), "js-on-event", "addEventListener(\"%s\")" % s)
    # --- return + line break
    for m in re.finditer(r"(?m)^([ \t]*)return[ \t]*\n(?:[ \t]*\n)*([ \t]+)(?=[\w$(\[{!'\"`-])", code):
        nxt = code[m.end():m.end() + 12]
        if len(m.group(2).expandtabs(4)) > len(m.group(1).expandtabs(4)) and not re.match(r"(case|default)\b", nxt):
            add(out, rel, _line(text, m.start()), "js-return-newline", "`return` alone on its line")
    # --- promises
    for m in re.finditer(r"\b(const|let|var)\s+([\w$]+)\s*=\s*fetch\s*\(", code):
        name = m.group(2)
        rest = code[m.end():m.end() + 3000]
        if not re.search(r"\b%s\s*\.\s*then\b|await\s+%s\b|return\s+%s\s*(;|\n|$)|Promise\.\w+\(" % ((re.escape(name),) * 3), rest) and \
                re.search(r"\b%s\s*\.\s*(json|text|ok|status|blob|headers)\b" % re.escape(name), rest):
            add(out, rel, _line(text, m.start()), "js-fetch-not-awaited", "`%s = fetch(...)` used without await" % name)
    for m in re.finditer(r"(?<![\w$.])fetch\s*\(", code):
        end = _match_close(code, m.end() - 1)
        before = code[max(0, m.start() - 12):m.start()]
        if re.match(r"\s*\.\s*(json|text|blob)\s*\(", code[end + 1:end + 20]) and "await" not in before:
            add(out, rel, _line(text, m.start()), "js-fetch-not-awaited", "`fetch(...).json()` - the Promise has no .json()")
    for m in re.finditer(r"\.forEach\s*\(\s*async\b", code):
        add(out, rel, _line(text, m.start()), "js-foreach-async", "`.forEach(async ...)`")
    for m in re.finditer(r"\buse(Layout)?Effect\s*\(\s*async\b", code):
        add(out, rel, _line(text, m.start()), "js-async-effect", "`useEffect(async ...)`")
    # --- handlers called instead of passed
    if is_jsx:
        for m in re.finditer(r"\bon[A-Z]\w*\s*=\s*\{\s*([A-Za-z_$][\w$.]*)\s*\(", code):
            end = _match_close(code, m.end() - 1)
            if re.match(r"\s*\}", code[end + 1:end + 4]) and not HANDLER_FACTORY.search(m.group(1).split(".")[-1]):
                add(out, rel, _line(text, m.start()), "js-handler-called", "`%s` runs while the page is drawn" % " ".join(text[m.start():end + 2].split())[:50])
    for m in re.finditer(r"\b(setTimeout|setInterval)\s*\(\s*([A-Za-z_$][\w$.]*)\s*\(", code):
        end = _match_close(code, m.end() - 1)
        if re.match(r"\s*,", code[end + 1:end + 4]) and m.group(2) not in ("function", "async") and not HANDLER_FACTORY.search(m.group(2).split(".")[-1]):
            add(out, rel, _line(text, m.start()), "js-handler-called", "`%s(%s(...), ...)` runs it now" % (m.group(1), m.group(2)))
    for m in re.finditer(r"\baddEventListener\s*\(\s*(['\"])\s*\1\s*,\s*([A-Za-z_$][\w$.]*)\s*\(", code):
        end = _match_close(code, m.end() - 1)
        if re.match(r"\s*[,)]", code[end + 1:end + 4]) and not HANDLER_FACTORY.search(m.group(2).split(".")[-1]):
            add(out, rel, _line(text, m.start()), "js-handler-called", "`addEventListener(..., %s(...))` runs it now" % m.group(2))
    # --- loops
    for m in re.finditer(r"\bfor\s*\(\s*(?:let|var)?\s*([\w$]+)\s*=\s*0\s*;\s*\1\s*<=\s*([\w$.]+)\.length\s*;", code):
        body = code[m.end():m.end() + 2000]
        if re.search(r"\b%s\s*\[\s*%s\s*\]" % (re.escape(m.group(2)), re.escape(m.group(1))), body):
            add(out, rel, _line(text, m.start()), "js-off-by-one", "`%s <= %s.length`" % (m.group(1), m.group(2)))
    # --- results thrown away (a statement that is only `x.trim()` etc.)
    for m in re.finditer(r"(?m)^[ \t]*([\w$]+(?:\.[\w$]+)*)\.(%s)\s*\(" % "|".join(NO_EFFECT_JS), code):
        end = _match_close(code, m.end() - 1)
        after = code[end + 1:end + 40]
        prev = code[:m.start()].rstrip()
        args_ = code[m.end():end]
        if m.group(2) in ("replace", "replaceAll") and re.search(r"\bfunction\b|=>", args_):
            continue                                # replace(re, fn) used to walk through matches
        if re.search(r"(?i)(location|router|history|navigation|navigator|nav|editor|doc|range|selection|cm|view)$", m.group(1)):
            continue                                # location.replace(url) navigates; editor.replace(...) edits
        if re.match(r"[ \t]*;?[ \t]*(\n|$|//)", after) and not re.match(r"\s*\.", after) and (prev == "" or prev[-1] in ";{}") \
                and m.group(1) not in ("this", "super", "console", "Object", "Array", "Promise", "window", "document", "$", "jQuery", "_", "app", "router"):
            add(out, rel, _line(text, m.start()), "js-no-effect", "`%s.%s(...)` - the result is thrown away" % (m.group(1)[:30], m.group(2)))
    for m in re.finditer(r"[\w$\]\)]\s+=([+-])\s+[\w$(]", code):
        add(out, rel, _line(text, m.start()), "js-plus-equals", "`=%s` - did you mean `%s=`?" % (m.group(1), m.group(1)))
    # --- switch with the same case twice
    for m in re.finditer(r"\bswitch\s*\(", code):
        pe = _match_close(code, m.end() - 1)
        bs = code.find("{", pe)
        if bs < 0:
            continue
        be = _match_close(code, bs)
        seen, depth, k = {}, 0, bs + 1
        for cm in re.finditer(r"[{}]|\bcase\s+((['\"])\s*\2|[\w$.-]+)\s*:", code[bs + 1:be]):
            tok = cm.group(0)
            if tok == "{":
                depth += 1
                continue
            if tok == "}":
                depth -= 1
                continue
            if depth:
                continue
            val = cm.group(1)
            if cm.group(2):
                val = repr(string_at(bs + 1 + cm.start(1)))
            if val in seen:
                add(out, rel, _line(text, bs + 1 + cm.start()), "js-duplicate-case", "case %s also on line %d" % (val[:30], seen[val]))
            else:
                seen[val] = _line(text, bs + 1 + cm.start())
    # --- months from 0
    for m in re.finditer(r"\bnew\s+Date\s*\(\s*(\d{4})\s*,\s*(\d{1,2})\s*[,)]", code):
        if int(m.group(2)) >= 12:
            add(out, rel, _line(text, m.start()), "js-bad-month", "new Date(%s, %s, ...) - month %s does not exist" % (m.group(1), m.group(2), m.group(2)))
    return out


# =================================================================================================== HTML
INPUT_TYPES = {"button", "checkbox", "color", "date", "datetime-local", "email", "file", "hidden", "image", "month", "number",
               "password", "radio", "range", "reset", "search", "submit", "tel", "text", "time", "url", "week", "datetime"}
SCRIPT_TYPES = {"", "text/javascript", "module", "application/javascript", "application/ecmascript", "text/ecmascript",
                "application/x-javascript", "text/jsx", "text/babel", "text/typescript", "importmap", "speculationrules"}
TEMPLATE_MARK = re.compile(r"\{\{|\{%|<%|\$\{|\[\[|@\{|\{#")
NUMBER_FIELD = re.compile(r"(?i)(qty|quantity|amount|price|total|count|age|weight|stock|units|seats|guests|tickets|items|pay)")


CONTROL_FLOW = re.compile(r"\{%|<%|<\?|\{\{\s*[#/^]|\{#|@(if|for|switch|else|foreach|section)\b|\bng-template\b|\*ng(If|For)")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr", "keygen",
        "basefont", "bgsound", "frame", "image"}
KNOWN_TAGS = VOID | set("""a abbr address article aside audio b bdi bdo blockquote body button canvas caption cite code colgroup data
datalist dd del details dfn dialog div dl dt em fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 head header hgroup html i
iframe ins kbd label legend li main map mark menu meter nav noscript object ol optgroup option output p picture pre progress q rp rt
ruby s samp script search section select slot small span strong style sub summary sup table tbody td template textarea tfoot th
thead time title tr u ul var video center font marquee""".split())
CLOSES_P = set("""address article aside blockquote center details dialog dir div dl fieldset figcaption figure footer form h1 h2 h3 h4
h5 h6 header hgroup hr main menu nav ol pre search section summary ul listing xmp plaintext li dd dt""".split())
SCOPE_STOP = {"applet", "caption", "html", "table", "td", "th", "marquee", "object", "template", "svg", "math"}
HEAD_OK = {"meta", "link", "title", "style", "script", "base", "noscript", "template", "head", "html", "body"}
TABLE_PARTS = {"table", "tbody", "thead", "tfoot", "tr"}
TABLE_OK = {"caption", "colgroup", "col", "thead", "tbody", "tfoot", "tr", "td", "th", "script", "template", "style", "form"}
OLD_TAGS = {"center", "font", "marquee", "blink", "big", "strike", "tt", "frame", "frameset", "acronym", "applet", "basefont", "dir",
            "isindex", "spacer", "xmp", "listing", "plaintext"}
PLAIN_ATTRS = {"href", "src", "class", "id", "type", "name", "for", "rel", "action", "method", "width", "height", "lang", "target",
               "role", "srcset", "media", "charset"}


class _Page(HTMLParser):
    """Reads a page roughly the way a browser builds it, keeping track of which elements are open, so that the places
    where the browser silently REWRITES the page (ends a paragraph early, ignores a form, moves text out of a table)
    can be pointed out."""

    def __init__(self, quirks_unknown=True):
        super().__init__(convert_charrefs=True)
        self.tags = []              # (tag, [(name, value)], line, inside a form)
        self.scripts = []           # (attrs, code, first line)
        self.styles = []            # (css, first line)
        self.form_depth = 0
        self.in_script = None
        self.in_style = None
        self.buf = []
        self.decl = False
        self.stack = []             # open elements: (tag, line)
        self.nest = []              # (line, kind, detail)  - where the browser rewrites the page
        self.p_closer = None        # (tag, line, p line): a block that silently ended a paragraph
        self.unparsed = []          # (line, text) - a tag that never ends
        self.forms = []             # {"attrs", "line", "fields": [(tag, attrs, line)]}
        self.form_stack = []
        self.head_closer = None     # (tag, line) - a body tag that ended <head> early
        self.after_head_close = []  # head-only tags that came after it
        self.end_br = []
        self.headings = []          # (level, line)
        self.select = None          # [line, multiple, [selected option lines]]
        self.multi_selected = []    # (select line, option lines)

    # ---- helpers
    def _in(self, names, stop=SCOPE_STOP):
        for t, ln in reversed(self.stack):
            if t in names:
                return (t, ln)
            if t in stop:
                return None
        return None

    def _pop_to(self, names, stop=SCOPE_STOP):
        if self._in(names, stop) is None:
            return False
        while self.stack:
            t, _ = self.stack.pop()
            if t in names:
                return True
        return False

    def _foreign(self):
        return any(t in ("svg", "math") for t, _ in self.stack)

    def handle_decl(self, decl):
        if decl.lower().startswith("doctype"):
            self.decl = True

    def _structure(self, tag, line):
        if self._foreign():
            return
        top = self.stack[-1][0] if self.stack else ""
        if top in ("ul", "ol") and tag not in ("li", "script", "template"):
            self.nest.append((line, "html-list-child", "<%s> directly inside <%s>" % (tag, top)))
        if self._in({"head"}, {"body"}) and tag not in HEAD_OK:
            if self.head_closer is None:
                self.head_closer = (tag, line)
            self._pop_to({"head"}, {"html"})
        elif self.head_closer and tag in ("meta", "link", "title", "base"):
            self.after_head_close.append((tag, line))
        if re.fullmatch(r"h[1-6]", tag):
            self.headings.append((int(tag[1]), line))
        if top in TABLE_PARTS and tag not in TABLE_OK and not (tag == "input"):
            self.nest.append((line, "html-outside-table-cell", "<%s> directly inside <%s>" % (tag, top)))
        if tag in CLOSES_P or (tag == "table" and self.decl):
            pl = self._in({"p"}, SCOPE_STOP | {"button"})
            if pl:
                self._pop_to({"p"}, SCOPE_STOP | {"button"})
                self.p_closer = (tag, line, pl[1])
        if tag == "p":
            self.p_closer = None
        if tag == "a":
            outer = self._in({"a"}, {"table", "td", "th", "template", "caption"})
            if outer:
                self.nest.append((line, "html-link-in-link", "the link on line %d is still open" % outer[1]))
                self._pop_to({"a"}, {"table", "td", "th", "template", "caption"})
        if tag == "form":
            outer = self._in({"form"}, {"template"})
            if outer:
                self.nest.append((line, "html-form-in-form", "the form on line %d is still open" % outer[1]))
        if tag == "button":
            outer = self._in({"button"})
            if outer:
                self.nest.append((line, "html-button-in-button", "the button on line %d is still open" % outer[1]))
                self._pop_to({"button"})
        if tag in ("a", "button", "input", "select", "textarea") and (tag != "input" or True):
            host = self._in({"button", "a"} - ({tag} if tag in ("a", "button") else set()), {"table", "td", "th", "template", "svg"})
            if host and not (tag == "input" and host[0] == "a"):
                self.nest.append((line, "html-interactive-inside", "<%s> inside the <%s> from line %d" % (tag, host[0], host[1])))
        if tag == "li":
            if self._in({"li"}, SCOPE_STOP | {"ul", "ol", "menu"}):
                self._pop_to({"li"}, SCOPE_STOP | {"ul", "ol", "menu"})
            if not any(t in ("ul", "ol", "menu", "dir", "template") for t, _ in self.stack):
                self.nest.append((line, "html-li-outside-list", "<li>"))
        if tag in ("dd", "dt"):
            self._pop_to({"dd", "dt"}, SCOPE_STOP | {"dl"})
        if tag in ("option",):
            self._pop_to({"option"}, SCOPE_STOP | {"select", "datalist", "optgroup"})
        if tag == "optgroup":
            self._pop_to({"option", "optgroup"}, SCOPE_STOP | {"select"})
        if tag == "tr":
            self._pop_to({"td", "th", "tr"}, {"table", "tbody", "thead", "tfoot", "template"})
        if tag in ("td", "th"):
            self._pop_to({"td", "th"}, {"tr", "table", "template"})
        if tag in ("tbody", "thead", "tfoot"):
            self._pop_to({"td", "th", "tr", "tbody", "thead", "tfoot"}, {"table", "template"})

    def _options(self, tag, attrs, line):
        if tag == "select":
            self.select = [line, "multiple" in dict(attrs), []]
        elif tag == "option" and self.select is not None and "selected" in dict(attrs):
            self.select[2].append(line)

    def handle_starttag(self, tag, attrs):
        line = self.getpos()[0]
        self._options(tag, attrs, line)
        self.tags.append((tag, attrs, line, self.form_depth))
        self._structure(tag, line)
        self._form_field(tag, attrs, line)
        if tag == "form":
            self.form_depth += 1
        if tag == "script":
            self.in_script = (dict(attrs), line)
            self.buf = []
        if tag == "style":
            self.in_style = line
            self.buf = []
        if tag not in VOID:
            self.stack.append((tag, line))

    def _form_field(self, tag, attrs, line):
        if tag == "form" and not self.form_stack:
            self.forms.append({"attrs": dict(attrs), "line": line, "fields": []})
            self.form_stack.append(len(self.forms) - 1)
        elif tag in ("input", "select", "textarea") and self.form_stack:
            self.forms[self.form_stack[-1]]["fields"].append((tag, dict(attrs), line))

    def handle_startendtag(self, tag, attrs):
        line = self.getpos()[0]
        self.tags.append((tag, attrs, line, self.form_depth))
        self._form_field(tag, attrs, line)
        if tag in VOID and not self._foreign():
            self._structure(tag, line)
        if tag in VOID or self._foreign() or tag not in KNOWN_TAGS:
            return
        self.nest.append((line, "html-self-closing", "<%s ... />" % tag))
        self.handle_starttag_open(tag, attrs, line)

    def handle_starttag_open(self, tag, attrs, line):
        # <div/> really is an OPEN <div> for the browser - keep reading the page the way the browser does
        self._structure(tag, line)
        if tag == "form":
            self.form_depth += 1
        self.stack.append((tag, line))

    def handle_endtag(self, tag):
        line = self.getpos()[0]
        if tag == "select" and self.select is not None:
            if not self.select[1] and len(self.select[2]) > 1:
                self.multi_selected.append((self.select[0], self.select[2]))
            self.select = None
        if tag == "br" and not self._foreign():
            self.end_br.append(line)
        if tag == "form" and self.form_depth:
            self.form_depth -= 1
            if self.form_depth == 0 and self.form_stack:
                self.form_stack.pop()
        if tag == "script" and self.in_script:
            self.scripts.append((self.in_script[0], "".join(self.buf), self.in_script[1]))
            self.in_script = None
        if tag == "style" and self.in_style:
            self.styles.append(("".join(self.buf), self.in_style))
            self.in_style = None
        if tag == "p":
            if self._in({"p"}, SCOPE_STOP | {"button"}):
                self._pop_to({"p"}, SCOPE_STOP | {"button"})
            elif self.p_closer and not self._foreign():
                t, ln, pl = self.p_closer
                self.nest.append((ln, "html-p-closed-early", "<%s> inside the <p> from line %d" % (t, pl)))
                self.p_closer = None
            return
        stop = SCOPE_STOP - {tag}
        if tag in ("tr", "tbody", "thead", "tfoot"):
            stop = {"table", "template"}
        self._pop_to({tag}, stop)

    def handle_data(self, data):
        if self.in_script is not None or self.in_style is not None:
            self.buf.append(data)
            return
        if self.stack and self.stack[-1][0] in TABLE_PARTS and data.strip(" \t\r\n\f") and not self._foreign():
            self.nest.append((self.getpos()[0], "html-outside-table-cell",
                              "the text \"%s\" directly inside <%s>" % (data.strip()[:30], self.stack[-1][0])))


TAG_RE = re.compile(r"<[a-zA-Z][^\s/>]*(?:\s+[^\s=/>\"']+(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+))?)*\s*/?>")
_MASKS = [re.compile(r"<!--.*?-->", re.S), re.compile(r"<(script|style)\b.*?</\1\s*>", re.S | re.I), re.compile(r"<\?.*?\?>", re.S),
          re.compile(r"<%.*?%>", re.S), re.compile(r"\{\{.*?\}\}", re.S), re.compile(r"\{%.*?%\}", re.S),
          re.compile(r"\$\{[^}]*\}"), re.compile(r"<!\[CDATA\[.*?\]\]>", re.S), re.compile(r"<!doctype[^>]*>", re.I)]
_ENT = None


def _entities():
    global _ENT
    if _ENT is None:
        import html.entities
        full = {k[:-1] for k in html.entities.html5 if k.endswith(";")}
        legacy = sorted((k for k in html.entities.html5 if not k.endswith(";")), key=len, reverse=True)
        _ENT = (full, legacy)
    return _ENT


def _blank(m):
    return re.sub(r"[^\n]", " ", m.group(0))


def _html_text_traps(rel, text):
    """Character traps in the visible text of a page: unknown &codes;, &copy2024-style surprises, a stray >."""
    out = []
    code = text
    for rx in _MASKS:
        code = rx.sub(_blank, code)
    # a tag followed by one extra >   e.g.  <div class="box">>
    for m in TAG_RE.finditer(code):
        e = m.end()
        name = re.match(r"<([a-zA-Z][\w-]*)", m.group(0)).group(1)
        if code[e:e + 1] == ">" and code[e + 1:e + 2] != ">" and "=" in m.group(0) and \
                re.match(r"[ \t]*(\n|$)", code[e + 1:e + 40]) and not re.match(r"\s*</%s\s*>" % re.escape(name), code[e + 1:e + 80], re.I):
            add(out, rel, _line(text, e), "html-stray-character", "an extra > after %s" % m.group(0)[:40])
    textonly = TAG_RE.sub(_blank, code)
    full, legacy = _entities()
    shown = re.sub(r"<(code|pre|kbd|samp|xmp|textarea)\b.*?</\1\s*>", _blank, text, flags=re.S | re.I)
    for rx in _MASKS[1:]:
        shown = rx.sub(_blank, shown)
    shown = TAG_RE.sub(_blank, re.sub(r"<!--.*?-->", _blank, shown, flags=re.S))
    dbl = [m for m in re.finditer(r"&amp;([A-Za-z][A-Za-z0-9]*);", shown) if m.group(1) in full and m.group(1) != "amp"]
    if 0 < len(dbl) <= 3 and not re.search(r"&amp;#", shown):      # many of them = a page that SHOWS the codes on purpose
        m = dbl[0]
        add(out, rel, _line(text, m.start()), "html-double-escaped", "`%s` shows \"&%s;\" as text" % (m.group(0), m.group(1)))
    nocode = re.sub(r"<(script|style)\b.*?</\1\s*>", _blank, text, flags=re.S | re.I)
    pos = 0
    while True:
        a = nocode.find("<!--", pos)
        if a < 0:
            break
        b = nocode.find("-->", a + 4)
        body = nocode[a + 4:b if b >= 0 else len(nocode)]
        bad = re.search(r"--[ \t]+>|--!?\s+>", body)
        if bad and re.search(r"<[a-zA-Z]", body[bad.end():]):
            add(out, rel, _line(text, a + 4 + bad.start()), "html-comment-unclosed", "`%s` does not end the comment, so the "
                "page is hidden up to line %s" % (bad.group(0).strip(), _line(text, b) if b >= 0 else "the end"))
        elif b < 0 and re.search(r"<[a-zA-Z]", body):
            add(out, rel, _line(text, a), "html-comment-unclosed", "the comment is never closed - the rest of the page is hidden")
        if b < 0:
            break
        pos = b + 3
    for m in re.finditer(r"&([A-Za-z][A-Za-z0-9]*)(;?)", textonly):
        name, semi = m.group(1), m.group(2)
        line = _line(text, m.start())
        if semi:
            if name in full:
                continue
            hit = next((k for k in legacy if name.startswith(k)), None)
            if hit:
                add(out, rel, line, "html-bad-entity", "`&%s;` - the browser shows it as \"%s%s;\"" % (name, _unescape("&" + hit), name[len(hit):]))
                continue
            close = sorted((_typo_distance(name, k), k) for k in full if abs(len(k) - len(name)) <= 2)
            close = [k for dist, k in close if dist <= (1 if len(name) <= 3 else 2)][:1]
            add(out, rel, line, "html-bad-entity", "`&%s;`%s" % (name, (" - did you mean `&%s;`?" % close[0]) if close else ""))
        else:
            hit = next((k for k in legacy if name.startswith(k)), None)
            nxt = textonly[m.end():m.end() + 1]
            if hit and (len(name) > len(hit) or nxt == "="):
                add(out, rel, line, "html-ambiguous-entity", "`&%s%s` is shown as \"%s%s%s\"" % (name, nxt if nxt == "=" else "", _unescape("&" + hit), name[len(hit):], nxt if nxt == "=" else ""))
    return out


def _unescape(s):
    import html
    return html.unescape(s)


BOOLEAN_ATTRS = {"disabled", "checked", "readonly", "hidden", "required", "selected", "multiple", "autofocus", "novalidate",
                 "async", "defer", "open", "inert", "controls", "autoplay", "loop", "muted", "reversed", "nomodule",
                 "formnovalidate", "playsinline", "allowfullscreen", "default", "ismap", "itemscope"}
FIELD_NO_NAME_OK = {"submit", "button", "reset", "image"}
GARBLED = re.compile("â€[™œ”“˜–—¦¢¡¹ºšž]|â€\u009d"
                     "|Ã[©¨ª«¤¶¼±§¡³­º¸¢´»¹Ÿ–„œ]"
                     "|Â[ °©®·»«£½¼µ]")


MEDIA_TYPES = {"all", "screen", "print", "speech", "tv", "handheld", "projection", "braille", "embossed", "aural", "tty"}


def _bad_media(media):
    """'' if fine, else ' - did you mean ...?' text for an unknown media type."""
    for part in media.split(","):
        w = part.strip().lower()
        w = re.sub(r"^(only|not)\s+", "", w)
        m = re.match(r"([a-z][a-z-]*)\b", w)
        if not m or m.group(1) in MEDIA_TYPES or m.group(1) == "and":
            continue
        close = [t for t in sorted(MEDIA_TYPES) if _typo_distance(m.group(1), t) <= 2]
        return " - `%s` is not a media type%s" % (m.group(1), (", did you mean `%s`?" % close[0]) if close else "")
    return ""


def _html_more(rel, text, p, out):
    for tag, attrs, line, _ in p.tags:
        d = dict(attrs)
        if tag in KNOWN_TAGS:
            for a, v in attrs:
                if a in BOOLEAN_ATTRS and v is not None and v.strip().lower() in ("false", "no", "off", "0"):
                    add(out, rel, line, "html-boolean-false", "<%s %s=\"%s\">" % (tag, a, v))
        if d.get("aria-hidden", "").lower() == "true" and tag in ("a", "button", "input", "select", "textarea") \
                and d.get("tabindex") != "-1" and "disabled" not in d and not (tag == "a" and "href" not in d) \
                and (d.get("type") or "").lower() != "hidden":
            add(out, rel, line, "html-aria-hidden-focus", "<%s aria-hidden=\"true\">" % tag)
        if tag == "base" and d.get("href"):
            add(out, rel, line, "html-base-tag", "<base href=\"%s\">" % d["href"][:40])
    for f in p.forms:
        fa = f["attrs"]
        action = (fa.get("action") or "").strip()
        sends = action and not re.match(r"(#|javascript:)", action, re.I) and not TEMPLATE_MARK.search(action)
        if not sends:
            continue                     # handled by a script (fetch / FormData), which works without these
        files = [x for x in f["fields"] if x[0] == "input" and (x[1].get("type") or "").lower() == "file"]
        if files and ((fa.get("method") or "get").lower() != "post" or (fa.get("enctype") or "").lower() != "multipart/form-data"):
            add(out, rel, f["line"], "html-file-form", "<form action=\"%s\"> has a file field (line %d)" % (action[:30], files[0][2]))
        for tag, d, line in f["fields"]:
            if tag == "input" and (d.get("type") or "text").lower() in FIELD_NO_NAME_OK:
                continue
            if not d.get("name") and "form" not in d and not any(k.startswith((":", "[", "v-", "ng-", "x-", "wire:", "th:", "asp-")) for k in d):
                add(out, rel, line, "html-field-no-name", "<%s%s> in the form from line %d" % (
                    tag, (" id=\"%s\"" % d["id"][:20]) if d.get("id") else "", f["line"]))
    radios = {}
    for tag, attrs, line, in_form in p.tags:
        d = dict(attrs)
        if TEMPLATE_MARK.search(" ".join(str(v) for v in d.values() if v)):
            continue
        if d.get("media") is not None and tag in ("link", "style", "source"):
            bad = _bad_media(d["media"])
            if bad:
                add(out, rel, line, "html-bad-media", "media=\"%s\"%s" % (d["media"][:30], bad))
        if tag == "form" and d.get("method") is not None and d["method"].strip().lower() not in ("get", "post", "dialog"):
            add(out, rel, line, "html-bad-form-method", "method=\"%s\" (sent as GET)" % d["method"][:15])
        if tag == "button" and d.get("type") is not None and d["type"].strip().lower() not in ("submit", "button", "reset", "menu"):
            add(out, rel, line, "html-bad-button-type", "type=\"%s\" - this button SUBMITS the form" % d["type"][:15])
        if tag == "input" and d.get("min") and d.get("max"):
            lo, hi = d["min"].strip(), d["max"].strip()
            try:
                bigger = float(lo) > float(hi)
            except ValueError:
                bigger = len(lo) == len(hi) and re.fullmatch(r"[\d:T-]+", lo + hi) is not None and lo > hi
            if bigger:
                add(out, rel, line, "html-min-max", "min=\"%s\" max=\"%s\"" % (lo[:12], hi[:12]))
        if tag == "input" and (d.get("type") or "").lower() == "radio" and d.get("name"):
            radios.setdefault(d["name"].lower(), []).append((d["name"], line))
        if tag == "input" and (d.get("type") or "").lower() == "hidden" and in_form and \
                re.fullmatch(r"(?i)[\w-]*(price|amount|total|cost|subtotal|discount)[\w-]*", d.get("name") or "") and \
                not re.search(r"(?i)currency|_id$|-id$|code", d.get("name") or ""):
            add(out, rel, line, "html-hidden-price", "<input type=\"hidden\" name=\"%s\" value=\"%s\">" % (d["name"][:20], (d.get("value") or "")[:10]))
        for a in ("href", "src", "action", "poster", "data"):
            v = (d.get(a) or "").strip()
            if not v:
                continue
            if re.match(r"(?i)www\.[a-z0-9-]+\.[a-z]", v):
                add(out, rel, line, "html-no-scheme", "%s=\"%s\"" % (a, v[:40]))
            elif re.match(r"(?i)([a-z]:[\\/]|file:/|\\\\)", v):
                add(out, rel, line, "html-local-path", "%s=\"%s\"" % (a, v[:50]))
    for name, seen in radios.items():
        spellings = sorted({n for n, _ in seen})
        if len(spellings) > 1:
            first = seen[0][0]
            other = next(x for x in seen if x[0] != first)
            add(out, rel, other[1], "html-radio-name", "name=\"%s\" here, but name=\"%s\" on line %d" % (other[0], first, seen[0][1]))
    for sel_line, opts in p.multi_selected:
        add(out, rel, opts[-1], "html-multi-selected", "the <select> from line %d has %d selected options" % (sel_line, len(opts)))
    ids = {(dict(a).get("id") or "") for t, a, _, _ in p.tags}
    for tag, attrs, line, _ in p.tags:
        f = dict(attrs).get("form")
        if f and tag in ("input", "button", "select", "textarea", "fieldset", "output", "object") and f not in ids:
            add(out, rel, line, "html-form-missing", "form=\"%s\" - no form has that id" % f[:30])
    if p.head_closer and p.after_head_close:
        t, ln = p.head_closer
        add(out, rel, ln, "html-tag-in-head", "<%s> - and <%s> on line %d ends up in the body" % (t, p.after_head_close[0][0], p.after_head_close[0][1]))
    for ln in p.end_br:
        add(out, rel, ln, "html-end-br", "</br>")
    if p.headings:
        prev = None
        for lvl, ln in p.headings:
            if prev is not None and lvl > prev + 2:
                add(out, rel, ln, "html-heading-skip", "<h%d> right after <h%d>" % (lvl, prev))
                break
            prev = lvl
    tagnames = {t[0] for t in p.tags}
    if "head" in tagnames and not any(t[0] == "meta" and ("charset" in dict(t[1]) or (dict(t[1]).get("http-equiv") or "").lower() == "content-type")
                                      for t in p.tags):
        visible = re.sub(r"<(script|style)\b.*?</\1\s*>|<!--.*?-->", " ", text, flags=re.S | re.I)
        if re.search(r"[^\x00-\x7f]", visible):
            add(out, rel, 1, "html-no-charset", "")


def check_garbled(rel, text, out):
    """Mojibake: characters saved as UTF-8 and read back as Windows-1252 (or the other way round)."""
    for m in GARBLED.finditer(text):
        ctx = text[max(0, m.start() - 60):m.end() + 60]
        if re.search(r"mojibake|encod|decod|garbl|latin-?1|cp1252|windows-1252|charset", ctx, re.I):
            continue                         # text ABOUT the problem (a fix list, a test), not the problem
        a = text.rfind("\n", 0, m.start()) + 1
        snippet = re.sub(r"<[^>]*>", " ", text[max(a, m.start() - 25):m.end() + 25]).replace("\n", " ").strip()
        add(out, rel, _line(text, m.start()), "text-garbled", "`%s` in \"%s\"" % (m.group(0), snippet))
        return

def check_html(rel, text):
    out = []
    p = _Page()
    try:
        p.feed(text)
        rest = p.rawdata[p.rawdata.find("<") if "<" in p.rawdata else 0:]
        m = re.match(r"<([a-zA-Z][\w-]*)\s[^>]*?=\s*([\"'])", rest)
        if m and m.group(2) not in rest[m.end():]:      # the page ends inside a tag: its quote is never closed
            p.unparsed.append((p.getpos()[0], rest[:40].replace("\n", " ")))
        p.close()
    except Exception:
        return out, p
    templated = bool(TEMPLATE_MARK.search(text))
    ids = {}
    tagnames = [t[0] for t in p.tags]
    for tag, attrs, line, in_form in p.tags:
        names = [a for a, _ in attrs]
        dup = {a for a in names if names.count(a) > 1}
        for a in sorted(dup) if not templated else []:
            add(out, rel, line, "html-duplicate-attribute", "<%s %s=... %s=...>" % (tag, a, a))
        d = dict(attrs)
        i = (d.get("id") or "").strip()
        if i and not TEMPLATE_MARK.search(i):
            if i in ids and not templated:
                add(out, rel, line, "html-duplicate-id", "id=\"%s\" is also used on line %d" % (i[:30], ids[i]))
            ids.setdefault(i, line)
        if tag == "input":
            t = (d.get("type") or "text").strip().lower()
            if t not in INPUT_TYPES and not TEMPLATE_MARK.search(t):
                add(out, rel, line, "html-bad-input-type", "type=\"%s\"" % t[:20])
            if t == "number" and "min" not in d and NUMBER_FIELD.search((d.get("name") or "") + " " + (d.get("id") or "")):
                add(out, rel, line, "html-negative-number", "<input type=\"number\" name=\"%s\"> has no min" % (d.get("name") or d.get("id") or "")[:30])
        if tag == "button" and in_form and "type" not in d and any(a.startswith("on") for a in d):
            add(out, rel, line, "html-button-submits", "<button onclick=...> inside a form, without type")
        if tag == "script":
            t = (d.get("type") or "").strip().lower()
            if t not in SCRIPT_TYPES and re.search(r"java|script|ecma|modul", t):
                add(out, rel, line, "html-bad-script-type", "type=\"%s\"" % t[:30])
        if tag == "img" and "alt" not in d:
            add(out, rel, line, "html-img-alt", "<img src=\"%s\">" % (d.get("src") or "")[:40])
    if not templated:
        for tag, attrs, line, _ in p.tags:
            d = dict(attrs)
            if tag == "label" and d.get("for") and d["for"] not in ids:
                add(out, rel, line, "html-label-target", "<label for=\"%s\"> - no element has that id" % d["for"][:30])
    full_page = "html" in tagnames or "head" in tagnames
    if full_page and not templated:
        if not p.decl:
            add(out, rel, 1, "html-no-doctype", "")
        html_tag = next((t for t in p.tags if t[0] == "html"), None)
        if html_tag and "lang" not in dict(html_tag[1]):
            add(out, rel, html_tag[2], "html-no-lang", "")
        if "head" in tagnames:
            if not any(t[0] == "meta" and (dict(t[1]).get("name") or "").lower() == "viewport" for t in p.tags):
                add(out, rel, 1, "html-no-viewport", "")
            if "title" not in tagnames:
                add(out, rel, 1, "html-no-title", "")
    if not CONTROL_FLOW.search(text):
        broken = {ln for ln, _ in p.unparsed} | {ln for t, attrs, ln, _ in p.tags for a, v in attrs if v and a in PLAIN_ATTRS and re.search(r"<[/a-zA-Z!]", v)}
        for line, kind, detail in p.nest:
            m = re.search(r"line (\d+)", detail)
            if m and int(m.group(1)) in broken:
                continue                        # only an echo of the quote that was never closed
            add(out, rel, line, kind, detail)
        for line, snippet in p.unparsed:
            add(out, rel, line, "html-unclosed-quote", "`%s` - the quote is never closed" % snippet)
    quote_broken = {ln for ln, _ in p.unparsed} | {ln for t, at, ln, _ in p.tags for a, v in at
                                                   if v and a in PLAIN_ATTRS and re.search(r"<[/a-zA-Z!]", v)}
    for tag, attrs, line, _ in p.tags:
        for a, v in attrs:
            if a and set(a) <= set("\"'"):
                add(out, rel, line, "html-stray-character", "an extra %s in <%s ...>" % (a, tag))
            elif a and re.search(r"[\"']", a) and not templated and line not in quote_broken:
                prev = next(((x, y) for x, y in reversed(attrs[:attrs.index((a, v))]) if y is not None), None)
                add(out, rel, line, "html-nested-quote", ("%s=\"%s\" ends before %s" % (prev[0], prev[1][:25], a.strip("\"'")[:20]))
                    if prev else "<%s ... %s>" % (tag, a[:20]))
            elif a in PLAIN_ATTRS and v and not TEMPLATE_MARK.search(v) and not re.match(r"\s*(data|javascript):", v, re.I) and \
                    (re.search(r"<[/a-zA-Z!]", v) or re.search(r"[\"']\s+[a-zA-Z-]+\s*=\s*[\"']", v)):
                add(out, rel, line, "html-unclosed-quote", "%s=\"%s...\" on <%s>" % (a, v.replace("\n", " ")[:25], tag))
        if tag == "meta" and (dict(attrs).get("name") or "").lower() == "viewport" and \
                re.search(r"user-scalable\s*=\s*(no|0)\b|maximum-scale\s*=\s*1(\.0*)?(?![\d.])", dict(attrs).get("content") or "", re.I):
            add(out, rel, line, "html-zoom-blocked", dict(attrs).get("content")[:60])
    if not CONTROL_FLOW.search(text):
        _html_more(rel, text, p, out)
    seen_old = set()
    for tag, attrs, line, _ in p.tags:
        if tag in OLD_TAGS and tag not in seen_old:
            seen_old.add(tag)
            add(out, rel, line, "html-old-tag", "<%s>" % tag)
    out += _html_text_traps(rel, text)
    for css, line in p.styles:
        for f in check_css(rel, css):
            f["line"] += line - 1
            out.append(f)
    # inline scripts are JavaScript too
    for attrs, code, line in p.scripts:
        t = (attrs.get("type") or "").lower()
        if attrs.get("src") or t not in SCRIPT_TYPES or not code.strip():
            continue
        for f in check_js(rel + ".js", code):
            f["line"] += line - 1
            f["file"] = rel
            out.append(f)
    return out, p


# =================================================================================================== CSS
CSS_PROPS = set("""
accent-color align-content align-items align-self align-tracks all anchor-name animation animation-composition animation-delay
animation-direction animation-duration animation-fill-mode animation-iteration-count animation-name animation-play-state
animation-range animation-timeline animation-timing-function appearance aspect-ratio backdrop-filter backface-visibility background
background-attachment background-blend-mode background-clip background-color background-image background-origin background-position
background-position-x background-position-y background-repeat background-size block-size border border-block border-block-color
border-block-end border-block-end-color border-block-end-style border-block-end-width border-block-start border-block-start-color
border-block-start-style border-block-start-width border-block-style border-block-width border-bottom border-bottom-color
border-bottom-left-radius border-bottom-right-radius border-bottom-style border-bottom-width border-collapse border-color
border-end-end-radius border-end-start-radius border-image border-image-outset border-image-repeat border-image-slice
border-image-source border-image-width border-inline border-inline-color border-inline-end border-inline-end-color
border-inline-end-style border-inline-end-width border-inline-start border-inline-start-color border-inline-start-style
border-inline-start-width border-inline-style border-inline-width border-left border-left-color border-left-style border-left-width
border-radius border-right border-right-color border-right-style border-right-width border-spacing border-start-end-radius
border-start-start-radius border-style border-top border-top-color border-top-left-radius border-top-right-radius border-top-style
border-top-width border-width bottom box-decoration-break box-shadow box-sizing break-after break-before break-inside caption-side
caret-color clear clip clip-path clip-rule color color-interpolation color-interpolation-filters color-scheme column-count column-fill
column-gap column-rule column-rule-color column-rule-style column-rule-width column-span column-width columns contain
contain-intrinsic-block-size contain-intrinsic-height contain-intrinsic-inline-size contain-intrinsic-size contain-intrinsic-width
container container-name container-type content content-visibility counter-increment counter-reset counter-set cursor cx cy d direction
display dominant-baseline empty-cells fill fill-opacity fill-rule filter flex flex-basis flex-direction flex-flow flex-grow flex-shrink
flex-wrap float flood-color flood-opacity font font-display font-family font-feature-settings font-kerning font-language-override
font-optical-sizing font-palette font-size font-size-adjust font-stretch font-style font-synthesis font-variant font-variant-alternates
font-variant-caps font-variant-east-asian font-variant-ligatures font-variant-numeric font-variant-position font-variation-settings
font-weight forced-color-adjust gap grid grid-area grid-auto-columns grid-auto-flow grid-auto-rows grid-column grid-column-end
grid-column-gap grid-column-start grid-gap grid-row grid-row-end grid-row-gap grid-row-start grid-template grid-template-areas
grid-template-columns grid-template-rows hanging-punctuation height hyphenate-character hyphens image-orientation image-rendering
inline-size inset inset-block inset-block-end inset-block-start inset-inline inset-inline-end inset-inline-start isolation
justify-content justify-items justify-self left letter-spacing lighting-color line-break line-clamp line-height list-style
list-style-image list-style-position list-style-type margin margin-block margin-block-end margin-block-start margin-bottom
margin-inline margin-inline-end margin-inline-start margin-left margin-right margin-top marker marker-end marker-mid marker-start
mask mask-border mask-clip mask-composite mask-image mask-mode mask-origin mask-position mask-repeat mask-size mask-type math-depth
math-style max-block-size max-height max-inline-size max-width min-block-size min-height min-inline-size min-width mix-blend-mode
object-fit object-position offset offset-anchor offset-distance offset-path offset-position offset-rotate opacity order orphans outline
outline-color outline-offset outline-style outline-width overflow overflow-anchor overflow-block overflow-clip-margin overflow-inline
overflow-wrap overflow-x overflow-y overscroll-behavior overscroll-behavior-block overscroll-behavior-inline overscroll-behavior-x
overscroll-behavior-y padding padding-block padding-block-end padding-block-start padding-bottom padding-inline padding-inline-end
padding-inline-start padding-left padding-right padding-top page page-break-after page-break-before page-break-inside paint-order
perspective perspective-origin place-content place-items place-self pointer-events position position-anchor position-area
position-try print-color-adjust quotes r resize right rotate row-gap ruby-align ruby-position rx ry scale scroll-behavior scroll-margin
scroll-margin-block scroll-margin-bottom scroll-margin-inline scroll-margin-left scroll-margin-right scroll-margin-top scroll-padding
scroll-padding-block scroll-padding-bottom scroll-padding-inline scroll-padding-left scroll-padding-right scroll-padding-top
scroll-snap-align scroll-snap-stop scroll-snap-type scroll-timeline scrollbar-color scrollbar-gutter scrollbar-width
shape-image-threshold shape-margin shape-outside shape-rendering speak stop-color stop-opacity stroke stroke-dasharray
stroke-dashoffset stroke-linecap stroke-linejoin stroke-miterlimit stroke-opacity stroke-width tab-size table-layout text-align
text-align-last text-anchor text-combine-upright text-decoration text-decoration-color text-decoration-line text-decoration-skip-ink
text-decoration-style text-decoration-thickness text-emphasis text-emphasis-color text-emphasis-position text-emphasis-style
text-indent text-justify text-orientation text-overflow text-rendering text-shadow text-size-adjust text-transform text-underline-offset
text-underline-position text-wrap text-wrap-mode text-wrap-style top touch-action transform transform-box transform-origin
transform-style transition transition-behavior transition-delay transition-duration transition-property transition-timing-function
translate unicode-bidi user-select vector-effect vertical-align view-timeline view-transition-name visibility white-space
white-space-collapse widows width will-change word-break word-spacing word-wrap writing-mode x y z-index zoom src unicode-range
size marks bleed ascent-override descent-override line-gap-override size-adjust font-named-instance base-palette override-colors
syntax inherits initial-value navigation types field-sizing interpolate-size reading-flow text-spacing-trim text-autospace
""".split())
CSS_LENGTH_PROPS = set("""width height min-width min-height max-width max-height margin margin-top margin-right margin-bottom margin-left
padding padding-top padding-right padding-bottom padding-left top right bottom left font-size border-width border-radius gap row-gap
column-gap letter-spacing word-spacing outline-width outline-offset text-indent flex-basis border-top-width border-right-width
border-bottom-width border-left-width inset""".split())


def _typo_distance(a, b, limit=2):
    """Number of single-letter mistakes (wrong, missing, extra, or two letters swapped) between a and b."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if prev2 is not None and i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        prev2, prev = prev, cur
    return prev[-1]


def _closest_prop(prop):
    allowed = 1 if len(prop) <= 7 else 2
    best = min(CSS_PROPS, key=lambda k: (_typo_distance(prop, k, allowed), k))
    return best if _typo_distance(prop, best, allowed) <= allowed else None


def _css_strip(text):
    out = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)
    return re.sub(r"(['\"])(?:\\.|(?!\1).)*\1", lambda m: m.group(1) + " " * (len(m.group(0)) - 2) + m.group(1), out)


def _decls(body, base_off):
    """[(offset, prop, value)] of the declarations in one { } block."""
    res, depth, start = [], 0, 0
    for k, ch in enumerate(body + ";"):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == ";" and depth <= 0:
            part = body[start:k]
            if ":" in part:
                prop, val = part.split(":", 1)
                if prop.strip() and re.fullmatch(r"\s*-?-?[A-Za-z][\w-]*\s*", prop):
                    res.append((base_off + start + (len(part) - len(part.lstrip())), prop.strip().lower(), val.strip()))
            start = k + 1
    return res


TABLE_TR = re.compile(r"\btable\s*>\s*tr\b(?![\w-])")


CSS_GLOBAL = {"inherit", "initial", "unset", "revert", "revert-layer"}
_ALIGN = {"normal", "stretch", "center", "start", "end", "flex-start", "flex-end", "self-start", "self-end", "left", "right",
          "baseline", "first", "last", "safe", "unsafe", "space-between", "space-around", "space-evenly", "anchor-center", "legacy"}
CSS_KEYWORDS = {
    "display": {"block", "inline", "inline-block", "flex", "inline-flex", "grid", "inline-grid", "none", "contents", "flow-root",
                "list-item", "table", "inline-table", "table-row", "table-cell", "table-column", "table-row-group",
                "table-header-group", "table-footer-group", "table-column-group", "table-caption", "ruby", "ruby-text",
                "run-in", "flow", "math", "ruby-base", "ruby-base-container", "ruby-text-container", "-webkit-box",
                "-webkit-inline-box", "-ms-flexbox", "-ms-inline-flexbox", "-moz-box", "-ms-grid", "-ms-inline-grid", "subgrid",
                "masonry", "grid-lanes", "inline-grid-lanes", "inline-list-item", "-webkit-flex", "-webkit-inline-flex", "-moz-inline-box", "-moz-inline-stack"},
    "position": {"static", "relative", "absolute", "fixed", "sticky", "-webkit-sticky"},
    "float": {"left", "right", "none", "inline-start", "inline-end"},
    "clear": {"left", "right", "both", "none", "inline-start", "inline-end"},
    "visibility": {"visible", "hidden", "collapse"},
    "overflow": {"visible", "hidden", "scroll", "auto", "clip", "overlay"},
    "overflow-x": {"visible", "hidden", "scroll", "auto", "clip", "overlay"},
    "overflow-y": {"visible", "hidden", "scroll", "auto", "clip", "overlay"},
    "box-sizing": {"border-box", "content-box", "padding-box"},
    "flex-direction": {"row", "row-reverse", "column", "column-reverse"},
    "flex-wrap": {"nowrap", "wrap", "wrap-reverse"},
    "justify-content": _ALIGN, "align-items": _ALIGN, "align-self": _ALIGN | {"auto"}, "align-content": _ALIGN,
    "justify-items": _ALIGN, "justify-self": _ALIGN | {"auto"}, "place-items": _ALIGN, "place-content": _ALIGN,
    "text-align": {"left", "right", "center", "justify", "start", "end", "match-parent", "justify-all", "-webkit-center",
                   "-moz-center", "-webkit-left", "-webkit-right", "-khtml-center"},
    "text-transform": {"none", "uppercase", "lowercase", "capitalize", "full-width", "full-size-kana", "math-auto"},
    "font-style": {"normal", "italic", "oblique"},
    "white-space": {"normal", "nowrap", "pre", "pre-wrap", "pre-line", "break-spaces", "collapse", "preserve", "preserve-breaks",
                    "preserve-spaces", "wrap", "-moz-pre-wrap", "-o-pre-wrap", "-pre-wrap"},
    "pointer-events": {"auto", "none", "visiblepainted", "visiblefill", "visiblestroke", "visible", "painted", "fill", "stroke",
                       "all", "bounding-box"},
    "object-fit": {"fill", "contain", "cover", "none", "scale-down"},
    "resize": {"none", "both", "horizontal", "vertical", "block", "inline"},
    "user-select": {"none", "auto", "text", "all", "contain", "element"},
    "table-layout": {"auto", "fixed"},
    "border-collapse": {"collapse", "separate"},
    "list-style-position": {"inside", "outside"},
    "backface-visibility": {"visible", "hidden"},
    "direction": {"ltr", "rtl"},
    "word-break": {"normal", "break-all", "keep-all", "break-word", "auto-phrase"},
    "overflow-wrap": {"normal", "break-word", "anywhere"},
    "scroll-behavior": {"auto", "smooth"},
}
CSS_UNITS = {"px", "em", "rem", "vh", "vw", "vmin", "vmax", "vb", "vi", "svh", "lvh", "dvh", "svw", "lvw", "dvw", "svmin", "svmax",
             "lvmin", "lvmax", "dvmin", "dvmax", "ch", "ex", "cap", "ic", "lh", "rlh", "rex", "rch", "ric", "rcap", "cm", "mm", "q",
             "in", "pt", "pc", "deg", "rad", "grad", "turn", "s", "ms", "hz", "khz", "dpi", "dpcm", "dppx", "x", "fr", "cqw", "cqh",
             "cqi", "cqb", "cqmin", "cqmax", "e"}
UNIT_FREE_PROPS = re.compile(r"^(font-family|content|grid-template-areas|grid-area|animation|animation-name|counter-|will-change|"
                             r"font-feature-settings|font-variation-settings|quotes|src|unicode-range|transition-property|"
                             r"list-style-type|font$|mask|filter|-)")


def _css_depths(code):
    """brace depth at every position of the (comment/string-blanked) CSS"""
    d, out = 0, []
    for ch in code:
        out.append(d)
        if ch == "{":
            d += 1
        elif ch == "}":
            d = max(0, d - 1)
    return out


def check_css_more(rel, text, code, out):
    code_nourl = re.sub(r"url\([^)]*\)", lambda m: "url(" + " " * (len(m.group(0)) - 5) + ")", code)
    depth = _css_depths(code_nourl)
    # // comments
    for m in re.finditer(r"(?m)^[ \t]*//[^\n]*", code_nourl):
        if depth[m.start()] == 0:
            add(out, rel, _line(text, m.start()), "css-line-comment", "`%s` - the rule after it is thrown away" % m.group(0).strip()[:40])
        elif not m.group(0).rstrip().endswith(";"):
            add(out, rel, _line(text, m.start()), "css-line-comment", "`%s` - it swallows the next line" % m.group(0).strip()[:40])
    # @import after other rules
    seen_rule = None
    for m in re.finditer(r"@import\b|@charset\b|@layer\s+[\w\s,.-]+;|[^\s{};@][^{};]*\{", code_nourl):
        if m.group(0).startswith("@import"):
            if seen_rule is not None and depth[m.start()] == 0:
                add(out, rel, _line(text, m.start()), "css-late-import", "after the rule on line %d" % seen_rule)
        elif not m.group(0).startswith("@") and depth[m.start()] == 0 and seen_rule is None:
            seen_rule = _line(text, m.start())
    # media queries without units
    for m in re.finditer(r"@(media|container)\b[^{;]*", code_nourl):
        for q in re.finditer(r"\(\s*((?:min-|max-)?(?:width|height|inline-size|block-size))\s*:\s*(-?\d*\.?\d+)\s*\)", m.group(0)):
            if float(q.group(2)) != 0:
                add(out, rel, _line(text, m.start()), "css-media-no-unit", "`(%s: %s)` - %s what? (px, em ...)" % (q.group(1), q.group(2), q.group(2)))
    for m in re.finditer(r"\{([^{}]*)\}", code_nourl):
        for off, prop, val in _decls(m.group(1), m.start(1)):
            if prop.startswith("--"):
                continue
            line = _line(text, off)
            v = re.sub(r"\s*!important\s*$", "", val, flags=re.I)
            # two declarations run together (missing ;)
            mm = re.search(r"(?:^|\s)([a-z-]+)\s*:\s*\S", v)
            if mm and mm.group(1) in CSS_PROPS and "(" not in v[:mm.start()] and not re.search(r"\w:\w", mm.group(0)):
                add(out, rel, line, "css-missing-semicolon", "`%s: %s` - no ; before `%s`" % (prop, v[:mm.start()].strip()[:20], mm.group(1)))
                continue
            # calc() spacing
            for cm in re.finditer(r"\b(?:calc|clamp|min|max)\((.*)\)", v):
                inner = cm.group(1)
                inner = re.sub(r"var\([^()]*\)", "V", inner)
                if re.search(r"(\d|%|[a-zA-Z]|\))[+-](\d|\.\d|\(|V)", re.sub(r"\d[eE][+-]\d", "0", inner)) and \
                        not re.search(r"[a-zA-Z]-[a-zA-Z]", inner.replace("V", "")) or \
                        re.search(r"(\d|%|[a-zA-Z]|\))\s+[+-](\d|\.\d|V)", inner):
                    add(out, rel, line, "css-calc-spacing", "`%s`" % cm.group(0)[:40])
                    break
            # keywords
            allowed = CSS_KEYWORDS.get(prop)
            if allowed and "(" not in v and not TEMPLATE_MARK.search(v):
                for tok in re.split(r"[\s,]+", v.lower()):
                    if tok and tok not in allowed and tok not in CSS_GLOBAL and not tok.startswith(("-", "$")) and not re.match(r"\.?\d", tok):
                        close = sorted((_typo_distance(tok, k), k) for k in allowed)
                        hint = (" - did you mean `%s`?" % close[0][1]) if close and close[0][0] <= 2 else ""
                        add(out, rel, line, "css-bad-keyword", "`%s: %s`%s" % (prop, v[:30], hint))
                        break
            # units
            if not UNIT_FREE_PROPS.match(prop) and not TEMPLATE_MARK.search(v):
                vv = re.sub(r"url\([^)]*\)|#[0-9a-fA-F]+\b|\"[^\"]*\"|'[^']*'|var\([^()]*\)", " ", v)
                bad = None
                for um in re.finditer(r"(?<![\w.#-])(-?\d*\.?\d+)([a-zA-Z]+)\b", vv):
                    if um.group(2).lower() not in CSS_UNITS:
                        bad = "`%s` - %s is not a unit" % (um.group(0), um.group(2))
                        break
                if not bad:
                    um = re.search(r"(?<![\w.#-])(-?\d*\.?\d+)\s+(px|em|rem|vh|vw|%|ms|deg)\b", vv)
                    if um:
                        bad = "`%s` - the unit must touch the number" % um.group(0)
                if bad:
                    add(out, rel, line, "css-bad-unit", bad)


def css_variable_checks(texts):
    """var(--name) where --name is defined nowhere in the project"""
    out = []
    defined, uses, external = set(), [], False
    for rel, t in texts.items():
        ext = rel.rsplit(".", 1)[-1].lower()
        if ext not in ("css", "html", "htm", "js", "mjs", "jsx", "ts", "tsx", "vue", "svelte", "scss", "less"):
            continue
        defined |= set(re.findall(r"(--[A-Za-z0-9_-]+)\s*:", t))
        defined |= set(re.findall(r"@property\s+(--[\w-]+)", t))
        for fm in re.finditer(r"@function\s+--[\w-]+\s*\(([^)]*)\)", t):
            defined |= set(re.findall(r"--[\w-]+", fm.group(1)))   # @function --x(--size, --tint): its inputs
        defined |= set(re.findall(r"""setProperty\(\s*["'`](--[\w-]+)""", t))
        defined |= set(re.findall(r"""["'`](--[\w-]+)["'`]\s*[:\]]""", t))
        if ext in ("scss", "less", "vue", "svelte", "jsx", "tsx"):
            external = True                           # styles that are built or injected: can't see every definition
        if re.search(r"""<link[^>]+href=["'](https?:)?//[^"']+\.css|@import\s+(url\()?["']?(?!\.|/)[\w@]|import\s+["'][^."'/][^"']*\.css["']""", t):
            external = True
        if ext in ("css", "html", "htm"):
            code = _css_strip(t) if ext == "css" else t
            for m in re.finditer(r"var\(\s*(--[A-Za-z0-9_-]+)\s*\)", code):
                uses.append((rel, t, m))
    for rel, t, m in uses:
        name = m.group(1)
        if name in defined:
            continue
        close = sorted((_typo_distance(name, d), d) for d in defined if abs(len(d) - len(name)) <= 2)
        close = [d for dist, d in close if dist <= 2]
        if external and not close:
            continue                                  # may come from a framework or a library style sheet
        add(out, rel, _line(t, m.start()), "css-undefined-var", "`%s` is never defined%s" % (
            m.group(0), (" - did you mean `%s`?" % close[0]) if close else ""))
    return out


def check_css(rel, text):
    out = []
    code = _css_strip(text)
    if "#{" in code or re.search(r"(^|\n)\s*[$@][\w-]+\s*:", code):
        return out                                   # Sass / Less syntax inside a .css file - not plain CSS
    check_css_more(rel, text, code, out)
    for m in re.finditer(r"@media\s+([^{;]+)\{", code):
        bad = _bad_media(m.group(1))
        if bad:
            add(out, rel, _line(text, m.start()), "html-bad-media", "`@media %s`%s" % (m.group(1).strip()[:30], bad))
    positioned, zrules = set(), []
    for m in re.finditer(r"([^{};]*)\{([^{}]*)\}", code):
        sel = m.group(1).strip()
        props = {pr for _, pr, _ in _decls(m.group(2), 0)}
        if "position" in props:
            positioned |= set(re.findall(r"[.#][\w-]+", sel))
        if "z-index" in props and "position" not in props and not sel.startswith("@"):
            zrules.append((sel, m.start(2) + m.group(2).find("z-index")))
    flex_file = re.search(r"display\s*:\s*(inline-)?(flex|grid)", code)
    for sel, off in zrules:
        last = [re.split(r"[\s>+~]+", x.strip())[-1] for x in sel.split(",")]
        marks = set(re.findall(r"[.#][\w-]+", " ".join(last)))
        if marks and not (marks & positioned) and not flex_file and not re.search(r":(before|after)\b|dialog|popover|\[open\]", sel):
            add(out, rel, _line(text, off), "css-zindex-no-position", "`%s` has z-index but no position" % sel[:40])
    for m in TABLE_TR.finditer(re.sub(r"\{[^{}]*\}", lambda b: re.sub(r"[^\n]", " ", b.group(0)), code)):
        add(out, rel, _line(text, m.start()), "css-table-tr", "`%s`" % m.group(0).strip())
    for m in re.finditer(r"\{([^{}]*)\}", code):
        body = m.group(1)
        decls = _decls(body, m.start(1))
        seen = {}
        for off, prop, val in decls:
            line = _line(text, off)
            v = re.sub(r"\s*!important\s*$", "", val, flags=re.I)
            if prop.startswith("-"):
                continue
            if prop not in CSS_PROPS:
                close = _closest_prop(prop)
                if close:
                    add(out, rel, line, "css-unknown-property", "`%s` - did you mean `%s`?" % (prop, close))
                continue
            for hm in re.finditer(r"(?<![\w-])#([0-9A-Za-z]+)\b", re.sub(r"url\([^)]*\)", "", v)):
                h = hm.group(1)
                if len(h) not in (3, 4, 6, 8) or not re.fullmatch(r"[0-9a-fA-F]+", h):
                    add(out, rel, line, "css-bad-color", "`%s: %s`" % (prop, v[:40]))
                    break
            if prop in ("transition", "transition-duration", "transition-delay", "animation-duration", "animation-delay"):
                bare = [t for t in re.split(r"[\s,]+", re.sub(r"\([^)]*\)", "", v)) if re.fullmatch(r"-?(\d+\.?\d*|\.\d+)", t) and float(t) != 0]
                if bare:
                    add(out, rel, line, "css-missing-time-unit", "`%s: %s` - %s what? (s or ms)" % (prop, v[:30], bare[0]))
            if prop in CSS_LENGTH_PROPS and "(" not in v:
                bad = [t for t in v.split() if re.fullmatch(r"-?(\d+\.?\d*|\.\d+)", t) and float(t) != 0]
                if bad and not any(f["kind"] == "css-bad-unit" and f["line"] == line for f in out):
                    add(out, rel, line, "css-missing-unit", "`%s: %s` - %s what?" % (prop, v[:30], bad[0]))
            if prop in seen and seen[prop][1] != v and "(" not in v + seen[prop][1] and \
                    not re.search(r"-(webkit|moz|ms|o)-", v + seen[prop][1]) and "!important" not in (val + seen[prop][2]).lower():
                add(out, rel, line, "css-duplicate-property", "`%s` is also set on line %d (to %s)" % (prop, seen[prop][0], seen[prop][1][:20]))
            seen[prop] = (line, v, val)
    return out


# =================================================================================================== JSON
BUILD_DIRS = ("dist/", "build/", "lib/", "out/", "es/", "cjs/", "esm/", "umd/", ".next/", "public/build/")


def check_json(rel, text, project_root, files):
    out = []
    dups = []

    def hook(pairs):
        keys = [k for k, _ in pairs]
        for k in {k for k in keys if keys.count(k) > 1}:
            dups.append(k)
        return dict(pairs)
    try:
        data = json.loads(text, object_pairs_hook=hook)
    except ValueError:
        return out                                   # broken JSON is the syntax check's job
    for k in sorted(set(dups)):
        hits = [mm.start() for mm in re.finditer(r'"%s"\s*:' % re.escape(k), text)]
        add(out, rel, _line(text, hits[1]) if len(hits) > 1 else 1, "json-duplicate-key", "\"%s\"" % k[:40])
    if posixpath.basename(rel) == "package.json" and isinstance(data, dict):
        base = posixpath.dirname(rel)

        def exists(p):
            p = posixpath.normpath(posixpath.join(base, p.lstrip("./") if p.startswith("./") else p))
            return p in files or any(f.startswith(p.rstrip("/") + "/") for f in files) or \
                any(f == p + ext for f in files for ext in (".js", ".ts", ".mjs", ".cjs", "/index.js"))
        for key in ("main", "module", "browser"):
            v = data.get(key)
            if isinstance(v, str) and v and not v.lstrip("./").startswith(BUILD_DIRS) and not exists(v):
                hit = re.search(r'"%s"\s*:' % key, text)
                add(out, rel, _line(text, hit.start()) if hit else 1, "json-missing-file", "\"%s\": \"%s\"" % (key, v[:50]))
        for name, cmd in (data.get("scripts") or {}).items():
            if not isinstance(cmd, str):
                continue
            for sm in re.finditer(r"\b(?:node|nodemon|ts-node|tsx|python3?|deno run|bun)\s+(?:--?[\w-]+(?:=\S+)?\s+)*([\w./-]+\.(?:js|mjs|cjs|ts|py))\b", cmd):
                target = sm.group(1)
                if not target.lstrip("./").startswith(BUILD_DIRS) and not exists(target):
                    hit = re.search(r'"%s"\s*:' % re.escape(name), text)
                    add(out, rel, _line(text, hit.start()) if hit else 1, "json-missing-file", "script \"%s\" runs %s" % (name, target))
        nm = data.get("name")
        if isinstance(nm, str) and (nm != nm.lower() or " " in nm):
            hit = re.search(r'"name"\s*:', text)
            add(out, rel, _line(text, hit.start()) if hit else 1, "json-bad-package-name", "\"%s\"" % nm[:40])
        for sect in ("dependencies", "devDependencies"):
            for dep, ver in (data.get(sect) or {}).items():
                if isinstance(ver, str) and ver.strip() in ("*", "latest", "x", ""):
                    hit = re.search(r'"%s"\s*:' % re.escape(dep), text)
                    add(out, rel, _line(text, hit.start()) if hit else 1, "json-unpinned", "\"%s\": \"%s\"" % (dep, ver))
    return out


# =================================================================================================== Markdown
def _slug(h):
    h = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", h)                                    # pictures (badges) in headings
    h = re.sub(r"<[^>]+>|`|\*|\[([^\]]*)\]\([^)]*\)", lambda m: m.group(1) or "", h)
    h = re.sub(r"(?<![\w])_+(?=\S)|(?<=\S)_+(?![\w])", "", h).strip().lower()           # _emphasis_ is not part of the name
    h = re.sub(r"[^\w\- ]", "", h, flags=re.U)
    return h.replace(" ", "-")


def _md_parts(text):
    """(lines with code blocks/inline code blanked, [slugs of headings], unclosed fence line or None)"""
    lines = text.split("\n")
    out, slugs, counts = [], [], {}
    fence, fence_line = None, None
    for i, ln in enumerate(lines):
        fm = re.match(r"^ {0,3}(`{3,}|~{3,})", ln)
        if fence:
            out.append("")
            if fm and fm.group(1)[0] == fence[0] and len(fm.group(1)) >= len(fence) and not ln.strip()[len(fm.group(1)):].strip():
                fence = None
            continue
        if fm:
            fence, fence_line = fm.group(1), i + 1
            out.append("")
            continue
        clean = re.sub(r"`[^`\n]*`", lambda m: " " * len(m.group(0)), ln)
        out.append(clean)
        hm = re.match(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$", ln)
        if hm:
            s = _slug(hm.group(1))
            n = counts.get(s, 0)
            counts[s] = n + 1
            slugs.append(s if n == 0 else "%s-%d" % (s, n))
    for m in re.finditer(r"<[a-z][^>]*\b(?:name|id)\s*=\s*[\"']([^\"']+)", text, re.I):
        slugs.append(m.group(1).lower())
    for m in re.finditer(r"^\[\^([^\]]+)\]:", text, re.M):                              # footnotes
        slugs += ["fn-" + m.group(1).lower(), "user-content-fn-" + m.group(1).lower()]
    return out, slugs, (fence_line if fence else None)


def check_md(rel, text, files, slugs_of):
    out = []
    lines, slugs, open_fence = _md_parts(text)
    if open_fence:
        add(out, rel, open_fence, "md-unclosed-fence", "the ``` on line %d is never closed" % open_fence)
    base = posixpath.dirname(rel)
    has_headings = len(slugs) > 0
    for i, ln in enumerate(lines, 1):
        if has_headings and re.match(r"^#{1,6}[A-Za-z][^#\n]{2,}$", ln):
            add(out, rel, i, "md-heading-space", "`%s`" % ln[:40])
        for m in re.finditer(r"(?<!\])\[([^\]\n]+)\][ \t]+\((https?://[^)\s]+|\.{0,2}/?[\w./-]+\.(?:md|png|jpe?g|gif|svg|html|pdf))\)", ln):
            add(out, rel, i, "md-link-gap", "`[%s] (%s)`" % (m.group(1)[:20], m.group(2)[:30]))
        for m in re.finditer(r"!?\[(?:[^\[\]\n]|\[[^\]\n]*\])*\]\(([^)\n]*)\)", ln):
            raw = m.group(1).strip()
            if raw.startswith("<") and raw.endswith(">"):
                target = raw[1:-1]
            else:
                tm = re.match(r"^(\S+)(\s+([\"'(]).*)?$", raw)
                if not raw:
                    continue
                if not tm:
                    add(out, rel, i, "md-link-space", "`(%s)`" % raw[:40])
                    continue
                target = tm.group(1)
            if not target or re.match(r"^([a-z][a-z0-9+.-]*:|//)", target, re.I) or TEMPLATE_MARK.search(target):
                continue
            path, _, frag = target.partition("#")
            path = urllib.parse.unquote(path.split("?")[0])
            if not path:
                if frag and frag.lower() not in slugs and has_headings and not frag.lower().startswith(("fn-", "user-content-", "l")) or \
                        frag and frag.lower().startswith("l") and not re.fullmatch(r"l\d+(-l\d+)?", frag.lower()) and frag.lower() not in slugs and has_headings:
                    add(out, rel, i, "md-broken-anchor", "#%s" % frag[:40])
                continue
            full = posixpath.normpath(path.lstrip("/") if path.startswith("/") else posixpath.join(base, path))
            if full.startswith(".."):
                continue                             # outside the project (e.g. ../../issues on GitHub)
            if full not in files and not any(f.startswith(full.rstrip("/") + "/") for f in files):
                add(out, rel, i, "md-broken-link", "`%s` does not exist" % path[:50])
            elif frag and full.lower().endswith(".md") and full in slugs_of and slugs_of[full] and frag.lower() not in slugs_of[full]:
                add(out, rel, i, "md-broken-anchor", "%s#%s" % (path[:30], frag[:30]))
    return out


# =================================================================================================== across files
JS_LOOKUP = re.compile(r"""\b(?:getElementById\s*\(\s*(['"])([\w:-]+)\1|querySelector(?:All)?\s*\(\s*(['"])#([\w-]+)\3\s*\))""")
INLINE_HANDLER = re.compile(r"""\son[a-z]+\s*=\s*(["'])\s*([A-Za-z_$][\w$]*)\s*\(""", re.I)
GLOBAL_FUNCS = {"alert", "confirm", "prompt", "print", "close", "open", "setTimeout", "setInterval", "clearTimeout", "fetch",
                "eval", "parseInt", "parseFloat", "Number", "String", "Boolean", "encodeURIComponent", "decodeURIComponent",
                "scrollTo", "scroll", "blur", "focus", "stop", "postMessage", "requestAnimationFrame", "void", "if", "return"}


def check_project(texts, pages):
    """Rules that need the whole project: element ids looked up by scripts, functions called from onclick."""
    out = []
    web = {r: t for r, t in texts.items() if r.rsplit(".", 1)[-1].lower() in ("html", "htm", "js", "mjs", "cjs", "jsx", "ts", "tsx", "vue", "svelte")}
    has_markup = any(r.rsplit(".", 1)[-1].lower() in ("html", "htm", "jsx", "tsx", "vue", "svelte") for r in web)
    if has_markup:
        dynamic_ids = any(re.search(r"""\bid\s*=\s*\{(?!\s*["'])|\bid\s*=\s*["'][^"']*(\$\{|\{\{)|\.id\s*=\s*[^"'\s]|setAttribute\(\s*["']id["']\s*,\s*[^"'\s]""", t)
                          for t in web.values())
        if not dynamic_ids:
            all_web = "\n".join(web.values())
            for r, t in web.items():
                if r.rsplit(".", 1)[-1].lower() in ("html", "htm") and r not in pages:
                    continue
                for m in JS_LOOKUP.finditer(t):
                    name = m.group(2) or m.group(4)
                    pat = re.compile(r"""\bid\s*[=:]\s*\{?\s*["'`]%s["'`]|\.id\s*=\s*["'`]%s["'`]|setAttribute\(\s*["']id["']\s*,\s*["'`]%s["'`]|\bid=%s\b"""
                                     % ((re.escape(name),) * 4))
                    if not pat.search(all_web):
                        add(out, r, _line(t, m.start()), "html-missing-element", "#%s is not on any page" % name[:40])
    js_defs = "\n".join(t for r, t in web.items() if not r.lower().endswith((".html", ".htm"))) + "\n" + \
              "\n".join(code for p in pages.values() for _, code, _ in p.scripts)
    external = any(re.search(r"""<script[^>]+src\s*=\s*["'](https?:)?//""", t, re.I) for r, t in texts.items() if r.lower().endswith((".html", ".htm")))
    if not external:
        for r, t in texts.items():
            if not r.lower().endswith((".html", ".htm")) or TEMPLATE_MARK.search(t):
                continue
            for m in INLINE_HANDLER.finditer(t):
                fn = m.group(2)
                if fn in GLOBAL_FUNCS or t[m.end(2):m.end(2) + 1] == ".":
                    continue
                if not re.search(r"""\bfunction\s+%s\b|\b(?:const|let|var)\s+%s\b|\b%s\s*[:=]\s*(?:async\s*)?(?:function\b|\(|[\w$]+\s*=>)|window\.%s\s*=|\bclass\s+%s\b|^\s*(?:async\s+)?%s\s*\([^)]*\)\s*\{"""
                                 % ((re.escape(fn),) * 6), js_defs, re.M):
                    add(out, r, _line(t, m.start()), "html-missing-function", "%s() is not defined in any script" % fn)
    return out


# =================================================================================================== page + script together
VALUE_TAGS = {"input", "select", "textarea", "button", "output", "option", "li", "meter", "progress", "data", "param"}
DOM_GET = r"""(?:document\.)?(?:getElementById\(\s*["'`]([\w-]+)["'`]\s*\)|querySelector\(\s*["'`]#([\w-]+)["'`]\s*\))"""
SCRIPT_EXTS = ("js", "mjs", "cjs", "jsx", "ts", "tsx")


def _script_view(rel, text):
    """Only the JavaScript of a file (for a page: its <script> blocks), with line numbers unchanged."""
    if rel.rsplit(".", 1)[-1].lower() not in ("html", "htm"):
        return text
    keep = [" " if ch != "\n" else "\n" for ch in text]
    for m in re.finditer(r"<script\b([^>]*)>(.*?)</script\s*>", text, re.S | re.I):
        if re.search(r"""type\s*=\s*["']?(?!(text/javascript|module|application/javascript)\b)[\w/+-]""", m.group(1), re.I):
            continue
        keep[m.start(2):m.end(2)] = list(m.group(2))
    for m in re.finditer(r"""\son[a-z]+\s*=\s*"([^"]*)\"""", text):
        keep[m.start(1):m.end(1)] = list(m.group(1))
    return "".join(keep)


def deep_checks(texts, pages, files):
    out = []
    # which element each id belongs to (only ids that mean the same thing on every page)
    id_tag = {}
    for rel, page in pages.items():
        for tag, attrs, line, _ in page.tags:
            d = dict(attrs)
            if d.get("id") and not TEMPLATE_MARK.search(d["id"]):
                id_tag.setdefault(d["id"], set()).add((tag, (d.get("type") or "text").lower() if tag == "input" else ""))
    id_tag = {k: next(iter(v)) for k, v in id_tag.items() if len(v) == 1}
    all_code = {rel: _script_view(rel, t) for rel, t in texts.items()
                if rel.rsplit(".", 1)[-1].lower() in SCRIPT_EXTS + ("html", "htm") and not TEST_PATH.search(rel)}
    calls_submit = any(re.search(r"\.(submit|reset)\(\s*\)", c) for c in all_code.values())
    for rel, c in all_code.items():
        if not c.strip():
            continue
        names = {}
        for m in re.finditer(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*" + DOM_GET, c):
            names[m.group(1)] = m.group(2) or m.group(3)
        reassigned = {n for n in names if len(re.findall(r"\b%s\s*=[^=]" % re.escape(n), c)) > 1}
        for m in re.finditer(r"(?:" + DOM_GET + r"|\b([A-Za-z_$][\w$]*))\.value\b(?!As|Of)", c):
            idn = m.group(1) or m.group(2) or (names.get(m.group(3)) if m.group(3) not in reassigned else None)
            if not idn or idn not in id_tag:
                continue
            tag, typ = id_tag[idn]
            line = _line(c, m.start())
            if tag in KNOWN_TAGS and tag not in VALUE_TAGS and "-" not in tag:
                add(out, rel, line, "js-value-on-text", "#%s is a <%s>" % (idn, tag))
                continue
            before = c[max(0, m.start() - 40):m.start()]
            after = c[m.end():m.end() + 12]
            if tag == "input" and typ in ("checkbox", "radio") and \
                    (re.search(r"(\bif|\bwhile)\s*\(\s*!?\s*$|&&\s*$|\|\|\s*$", before) and re.match(r"\s*(\)|&&|\|\||\?)", after)):
                add(out, rel, line, "js-checkbox-value", "#%s is a checkbox - use .checked" % idn)
                continue
            if tag in ("input", "select", "textarea") and typ not in ("checkbox", "radio"):
                stmt_start = max(c.rfind(ch, 0, m.start()) for ch in ";{\n")
                left = c[stmt_start + 1:m.start()]
                right = c[m.end():m.end() + 30].split("\n")[0]
                if re.search(r"""["'`]""", left + right.split(";")[0]) or re.search(r"(Number|parseInt|parseFloat|\+)\s*\(?\s*$", left.rstrip()[:-1] if left.rstrip().endswith("+") else left):
                    continue
                num_left = re.search(r"([*/%-]\s*[\w.)\]]+|\b\d+(\.\d+)?)\s*\+\s*$", left)
                num_right = re.match(r"\s*\+\s*\d", right)
                if num_left or num_right:
                    add(out, rel, line, "js-value-plus", "`...%s` - .value is text" % c[m.start():m.end() + 6].split("\n")[0].strip()[:40])
        objs = {m.group(1) for m in re.finditer(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:\{|\[|new\s+(Map|Set|Date)\b)", c)}
        for m in re.finditer(r"\b(?:localStorage|sessionStorage)\.setItem\(\s*[^,()]+,\s*([A-Za-z_$][\w$]*|\{|\[)", c):
            v = m.group(1)
            if v in ("{", "[") or (v in objs and not re.search(r"\b%s\s*=\s*JSON\.stringify" % re.escape(v), c)):
                add(out, rel, _line(c, m.start()), "js-storage-object", "`%s` is an object/list" % v)
        for m in re.finditer(r"\.then\(\s*\(?\s*([A-Za-z_$][\w$]*)\s*\)?\s*=>\s*\1\.(json|text|blob|arrayBuffer|formData)\s*\)|"
                             r"=\s*await\s+([A-Za-z_$][\w$]*)\.(json|text|blob|arrayBuffer|formData)\s*;", c):
            add(out, rel, _line(c, m.start()), "js-json-not-called", "`.%s` without ()" % (m.group(2) or m.group(4)))
    # functions behind onclick that live only in modules
    scripts_text = "\n".join(all_code.values())
    for rel, page in pages.items():
        text = texts.get(rel, "")
        if TEMPLATE_MARK.search(text):
            continue
        classic, module = [], []
        for attrs, code_, _ in page.scripts:
            (module if (attrs.get("type") or "").lower() == "module" else classic).append(code_)
        for tag, attrs, line, _ in page.tags:
            d = dict(attrs)
            if tag == "script" and d.get("src") and not re.match(r"(?i)(https?:)?//", d["src"]):
                src = d["src"].split("?")[0]
                path = posixpath.normpath(src.lstrip("/") if src.startswith("/") else posixpath.join(posixpath.dirname(rel), src))
                (module if (d.get("type") or "").lower() == "module" else classic).append(texts.get(path, ""))
        mod, cls = "\n".join(module), "\n".join(classic)
        if not mod.strip():
            continue
        # read the handlers from the text itself, so that tags hidden by a BROKEN comment (-- >) count too
        visible = re.sub(r"<!--(?:(?!--\s+>).)*?-->", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)
        for hm in re.finditer(r"""\s(on[a-z]+)\s*=\s*"([^"]*)\"""", visible):
            a, v, line = hm.group(1), hm.group(2), _line(text, hm.start())
            if True:
                fm = re.match(r"\s*(?:return\s+)?([A-Za-z_$][\w$]*)\s*\(", v)
                if not fm or fm.group(1) in GLOBAL_FUNCS:
                    continue
                fn = re.escape(fm.group(1))
                define = r"\bfunction\s+%s\b|\b(?:const|let|var|class)\s+%s\b" % (fn, fn)
                if re.search(define, mod) and not re.search(define, cls) and \
                        not re.search(r"\b(window|globalThis|self)\s*\.\s*%s\s*=|Object\.assign\(\s*(window|globalThis)" % fn, scripts_text):
                    add(out, rel, line, "html-module-handler", "%s=\"%s\" - %s() is defined only inside a type=\"module\" script" % (a, v[:30], fm.group(1)))
    # links to pages and places that do not exist
    lower_files = {f.lower() for f in files}
    generated = any(posixpath.basename(f) in ("_config.yml", ".eleventy.js", "eleventy.config.js", "hugo.toml", "astro.config.mjs",
                                              "next.config.js", "gatsby-config.js", "svelte.config.js", "nuxt.config.ts") for f in files)
    for rel, page in pages.items():
        text = texts.get(rel, "")
        if TEMPLATE_MARK.search(text) or CONTROL_FLOW.search(text):
            continue
        here_ids = {(dict(a).get("id") or "") for _, a, _, _ in page.tags} | {(dict(a).get("name") or "") for t, a, _, _ in page.tags if t == "a"}
        for tag, attrs, line, in_form in page.tags:
            d = dict(attrs)
            if in_form and tag in ("input", "button", "select", "textarea") and calls_submit and \
                    ((d.get("name") or "") in ("submit", "reset") or (d.get("id") or "") in ("submit", "reset")):
                add(out, rel, line, "html-form-shadow", "%s=\"%s\" inside a form, and a script calls form.%s()" % (
                    "name" if d.get("name") in ("submit", "reset") else "id", d.get("name") or d.get("id"), d.get("name") or d.get("id")))
            if tag != "a" or not d.get("href"):
                continue
            href = d["href"].strip()
            if href.startswith("#"):
                frag = urllib.parse.unquote(href[1:])
                if frag and frag != "top" and not frag.startswith(("/", "!")) and frag not in here_ids and \
                        not re.search(r"""["'`]#?%s["'`]""" % re.escape(frag), scripts_text):
                    add(out, rel, line, "html-missing-anchor", "href=\"%s\" - no element on this page has id=\"%s\"" % (href[:30], frag[:30]))
                continue
            if generated or re.match(r"(?i)[a-z][a-z0-9+.-]*:|//|/", href):
                continue
            target = href.split("#")[0].split("?")[0]
            if not re.search(r"(?i)\.html?$", target):
                continue
            path = posixpath.normpath(posixpath.join(posixpath.dirname(rel), urllib.parse.unquote(target)))
            stem = path.rsplit(".", 1)[0]
            if path.lower() in lower_files or any((stem + e) in files for e in (".md", ".markdown", ".njk", ".pug", ".ejs", ".hbs", ".liquid", ".php")):
                continue
            add(out, rel, line, "html-missing-page", "href=\"%s\" - there is no %s" % (href[:40], path))
    return out

# =================================================================================================== run
def check(root):
    project = dedupe.Project(root)
    files = set(project.files)
    texts, found, pages = {}, [], {}
    for rel, size in project.files.items():
        ext = rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
        if ext not in ("py", "js", "mjs", "cjs", "jsx", "ts", "tsx", "html", "htm", "css", "json", "md", "markdown", "vue", "svelte") \
                or size > MAX_SIZE or rel.endswith((".min.js", ".min.css", ".d.ts")) or posixpath.basename(rel) in ("package-lock.json",):
            continue
        try:
            texts[rel] = (project.root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    slugs_of = {r: _md_parts(t)[1] for r, t in texts.items() if r.lower().endswith((".md", ".markdown"))}
    for rel, text in texts.items():
        ext = rel.rsplit(".", 1)[-1].lower()
        test = bool(TEST_PATH.search(rel))
        try:
            if ext == "py" and not test:
                found += check_python(rel, text)
            elif ext in ("js", "mjs", "cjs", "jsx", "ts", "tsx") and not test:
                found += check_js(rel, text)
            elif ext in ("html", "htm"):
                fs, page = check_html(rel, text)
                found += fs
                pages[rel] = page
            elif ext == "css":
                found += check_css(rel, text)
            elif ext == "json":
                found += check_json(rel, text, project.root, files)
            elif ext in ("md", "markdown"):
                found += check_md(rel, text, files, slugs_of)
        except Exception as e:                      # one odd file must never stop the whole check
            print("  (skipped %s: %s)" % (rel, type(e).__name__))
            continue
    try:
        found += check_project(texts, pages)
    except Exception:
        pass
    try:
        found += deep_checks(texts, pages, files)
    except Exception as e:
        print("  (page + script checks skipped: %s)" % type(e).__name__)
    for rel, t in texts.items():
        if rel.rsplit(".", 1)[-1].lower() in ("html", "htm", "md", "markdown", "json", "css", "js", "jsx", "ts", "tsx", "vue", "svelte") \
                and not TEST_PATH.search(rel):
            check_garbled(rel, t, found)
    # a form sent by a script (submit handler) reads its fields itself - names are not needed then
    if any(re.search(r"addEventListener\(\s*[\"'`]submit|onsubmit|preventDefault\s*\(|handleSubmit|@submit|\(ngSubmit\)|\.submit\(", t)
           for t in texts.values()):
        found = [f for f in found if f["kind"] != "html-field-no-name"]
    reads = set()
    for rel, t in texts.items():
        if rel.rsplit(".", 1)[-1].lower() in ("js", "mjs", "cjs", "jsx", "ts", "tsx", "html", "htm", "vue", "svelte"):
            reads |= set(re.findall(r"\bdataset\s*(?:\.\s*([A-Za-z_$][\w$]*)|\[\s*[\"'`]([^\"'`]+)[\"'`]\s*\])", t))
    reads = {a or b for a, b in reads}
    for rel, t in texts.items():
        if rel.rsplit(".", 1)[-1].lower() not in ("html", "htm") or CONTROL_FLOW.search(t):
            continue
        for m in re.finditer(r"\sdata-([a-z0-9]+(?:-[a-z0-9]+)*[A-Z][\w-]*)\s*=", t):
            name = m.group(1)
            camel = re.sub(r"-([a-z])", lambda x: x.group(1).upper(), name)
            if camel in reads and camel != re.sub(r"-([a-z])", lambda x: x.group(1).upper(), name.lower()):
                add(found, rel, _line(t, m.start()), "html-data-case", "data-%s is read as dataset.%s, which gives undefined" % (name, camel))
    # rows built by a script (createElement("tr") + appendChild) really can sit directly in a <table>
    try:
        found += css_variable_checks(texts)
    except Exception:
        pass
    builds_rows = any(re.search(r"createElement\(\s*[\"'`]tr[\"'`]", t) for t in texts.values())
    if builds_rows:
        found = [f for f in found if f["kind"] != "css-table-tr"]
    else:
        for rel, t in texts.items():
            if rel.rsplit(".", 1)[-1].lower() in ("js", "mjs", "cjs", "jsx", "ts", "tsx", "html", "htm", "vue", "svelte"):
                for m in re.finditer(r"(?:querySelector(?:All)?|closest|matches|\$|jQuery)\(\s*([\"'`])([^\"'`\n]*)\1", t):
                    if TABLE_TR.search(m.group(2)):
                        add(found, rel, _line(t, m.start()), "css-table-tr", "`%s`" % m.group(0)[:60])
    uniq, seen = [], set()
    for f in sorted(found, key=lambda f: (f["file"], f["line"], f["kind"])):
        key = (f["file"], f["line"], f["kind"], f["detail"])
        if key not in seen:
            seen.add(key)
            uniq.append(f)
    return len(texts), uniq


def main():
    ap = argparse.ArgumentParser(description="Arachnid bug hunter (looks only)")
    ap.add_argument("root", nargs="?", default=str(HERE.parent))
    ap.add_argument("--out", default=str(HERE / "reports" / "bugs-report.json"))
    ap.add_argument("--rules", action="store_true", help="list every rule")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    if a.rules:
        for k, (pile, title, why, todo) in RULES.items():
            print("%-28s [%s] %s\n%30s%s\n" % (k, pile, title, "", why))
        return 0
    print("Arachnid bug hunter - looking at:", Path(a.root).resolve())
    n, found = check(a.root)
    ledger.reset("bugs")
    for f in found:
        pile, title, why, todo = RULES[f["kind"]]
        what = title + (": " + f["detail"] if f["detail"] else "")
        print("\n  %s  line %d   [%s]  %s" % (f["file"], f["line"], "DECISION" if pile == "decision" else "IDEA", f["kind"]))
        print("     %s" % what)
        ledger.record("bugs", "found" if pile == "decision" else "idea", "warning" if pile == "decision" else "info",
                      f["file"], f["line"], what, why, todo, f["kind"])
    dec = sum(1 for f in found if RULES[f["kind"]][0] == "decision")
    print("\n---------------- Summary ----------------")
    print("Files read: %d    Hidden bugs to decide on: %d    Small ideas: %d" % (n, dec, len(found) - dec))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"root": str(Path(a.root).resolve()), "findings": found}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
