import warnings
from functools import wraps

# For marking deprecated functions and classes that should be avoided
def deprecated(reason: str = ""):
    def decorator(func_or_class):
        message = f"{func_or_class.__name__} is deprecated. {reason}"

        @wraps(func_or_class)
        def wrapper(*args, **kwargs):
            warnings.warn(
                message,
                DeprecationWarning,
                stacklevel=2
            )
            return func_or_class(*args, **kwargs)

        return wrapper
    return decorator
