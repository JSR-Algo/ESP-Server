package tbot.common.xss;

import org.jsoup.Jsoup;
import org.jsoup.safety.Safelist;

/**
 * XSSFilter utility class
 * Copyright (c) Renren Open Source All rights reserved.
 * Website: https://www.renren.io
 */
public class XssUtils extends Safelist {

    /**
     * XSSFilter
     */
    public static String filter(String html) {
        return Jsoup.clean(html, xssWhitelist());
    }

    /**
     * XSSFilter whitelist
     */
    private static Safelist xssWhitelist() {
        return new Safelist()
                // Supported tags
                .addTags("a", "b", "blockquote", "br", "caption", "cite", "code", "col", "colgroup", "dd", "div", "dl",
                        "dt", "em", "h1", "h2", "h3", "h4", "h5", "h6", "i", "img", "li", "ol", "p", "pre", "q",
                        "small",
                        "strike", "strong", "sub", "sup", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "u",
                        "ul",
                        "span")

                // Supported tag attributes
                .addAttributes("a", "href", "class", "style", "target", "rel", "nofollow")
                .addAttributes("blockquote", "cite")
                .addAttributes("code", "class", "style")
                .addAttributes("col", "span", "width")
                .addAttributes("colgroup", "span", "width")
                .addAttributes("img", "align", "alt", "height", "src", "title", "width", "class", "style")
                .addAttributes("ol", "start", "type")
                .addAttributes("q", "cite")
                .addAttributes("table", "summary", "width", "class", "style")
                .addAttributes("tr", "abbr", "axis", "colspan", "rowspan", "width", "style")
                .addAttributes("td", "abbr", "axis", "colspan", "rowspan", "width", "style")
                .addAttributes("th", "abbr", "axis", "colspan", "rowspan", "scope", "width", "style")
                .addAttributes("ul", "type", "style")
                .addAttributes("pre", "class", "style")
                .addAttributes("div", "class", "id", "style")

                .addAttributes("span", "class", "style")

                // Protocol corresponding to tag attribute
                .addProtocols("a", "href", "ftp", "http", "https", "mailto")
                .addProtocols("img", "src", "http", "https", "data")
                .addProtocols("blockquote", "cite", "http", "https")
                .addProtocols("cite", "cite", "http", "https")
                .addProtocols("q", "cite", "http", "https")
                .addProtocols("cite", "cite", "http", "https");
    }

}