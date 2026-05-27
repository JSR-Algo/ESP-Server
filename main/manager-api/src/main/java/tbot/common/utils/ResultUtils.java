package tbot.common.utils;

/**
 * Return response body utility class
 */
public class ResultUtils
{
    public static <T> Result<T> success(T data) {
        return new Result<T>().ok(data);
    }

    public static <T> Result<T> error() {
        return new Result<T>().error();
    }

    public static <T> Result<T> error(String msg) {
        return new Result<T>().error(msg);
    }

    public static <T> Result<T> error(int errorCode, String msg) {
        return new Result<T>().error(errorCode, msg);
    }

    public static <T> Result<T> error(int errorCode) {
        return new Result<T>().error(errorCode);
    }

    public static <T> Result<T> empty() {
        return new Result<T>();
    }
}
