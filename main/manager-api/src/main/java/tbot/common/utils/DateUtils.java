package tbot.common.utils;

import java.text.ParseException;
import java.text.SimpleDateFormat;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.temporal.ChronoUnit;
import java.util.Date;

/**
 * Date Processing
 * Copyright (c) Renren Open Source All rights reserved.
 * Website: https://www.renren.io
 */
public class DateUtils {
    /**
     * Time Format(yyyy-MM-dd)
     */
    public final static String DATE_PATTERN = "yyyy-MM-dd";
    /**
     * Time Format(yyyy-MM-dd HH:mm:ss)
     */
    public final static String DATE_TIME_PATTERN = "yyyy-MM-dd HH:mm:ss";
    public final static String DATE_TIME_MILLIS_PATTERN = "yyyy-MM-dd HH:mm:ss.SSS";


    /**
     * Date formatting, date format:yyyy-MM-dd
     *
     * @param date Date
     * @return Returnyyyy-MM-ddFormat Date
     */
    public static String format(Date date) {
        return format(date, DATE_PATTERN);
    }

    /**
     * Date formatting, date format:yyyy-MM-dd
     *
     * @param date    Date
     * @param pattern Format, such as:DateUtils.DATE_TIME_PATTERN
     * @return Returnyyyy-MM-ddFormat Date
     */
    public static String format(Date date, String pattern) {
        if (date != null) {
            SimpleDateFormat df = new SimpleDateFormat(pattern);
            return df.format(date);
        }
        return null;
    }

    /**
     * Date Parse
     *
     * @param date    Date
     * @param pattern Format, such as:DateUtils.DATE_TIME_PATTERN
     * @return ReturnDate
     */
    public static Date parse(String date, String pattern) {
        try {
            return new SimpleDateFormat(pattern).parse(date);
        } catch (ParseException e) {
            e.printStackTrace();
        }
        return null;
    }


    public static String getDateTimeNow() {
        return getDateTimeNow(DATE_TIME_PATTERN);
    }

    public static String getDateTimeNow(String pattern) {
        return format(new Date(), pattern);
    }

    public static String millsToSecond(long mills) {
        return String.format("%.3f", mills / 1000.0);
    }

    /**
     * Get short time string:10seconds agoReturnJust now, how manyseconds ago, severalhours ago, over one week return year month day hour minute second
     * @param date
     * @return
     */
    public static String getShortTime(Date date) {
        if (date == null) {
            return null;
        }
        // will Date Convert to Instant
        LocalDateTime localDateTime = date.toInstant()
                // Get system defaultTime zone
                .atZone(ZoneId.systemDefault())
                // Convert to LocalDateTime
                .toLocalDateTime();
        // Current Time
        LocalDateTime now = LocalDateTime.now();
        // Time difference, unit seconds
        long secondsBetween = ChronoUnit.SECONDS.between(localDateTime, now);

        if (secondsBetween <= 10) {
            return "Just now";
        } else if (secondsBetween < 60) {
            return secondsBetween + "seconds ago";
        } else if (secondsBetween < 60 * 60) {
            return secondsBetween / 60 + "minutes ago";
        } else if (secondsBetween < 86400) {
            return secondsBetween / 3600 + "hours ago";
        } else if (secondsBetween < 604800) {
            return secondsBetween / 86400 + "days ago";
        } else {
            // Over one week, show full date time
            return format(date,DATE_TIME_PATTERN);
        }
    }
}
