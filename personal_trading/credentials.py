"""Runtime-only named inputs. Never load dotenv, provider profiles, or a key file."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RuntimeCredentials:
    key: str = field(repr=False)
    secret: str = field(repr=False)

    @classmethod
    def from_environ(cls, environment):
        key = environment.get("ALPACA_API_KEY")
        secret = environment.get("ALPACA_API_SECRET")
        if any(type(value) is not str or not value.strip() for value in (key, secret)):
            raise ValueError("runtime requires ALPACA_API_KEY and ALPACA_API_SECRET")
        return cls(key, secret)
