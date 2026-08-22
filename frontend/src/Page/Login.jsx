import { useState } from "react";
import { useNavigate } from "react-router-dom";
import "../styles/Login.css";

function Login() {
  const navigate = useNavigate();

  const [role, setRole] = useState("junior");
  const [name, setName] = useState("");
  const [agentId, setAgentId] = useState("");
  const [passkey, setPasskey] = useState("");
  const [rememberDevice, setRememberDevice] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = (event) => {
    event.preventDefault();

    setError("");

    if (!name.trim() || !agentId.trim() || !passkey.trim()) {
      setError("Name, Agent ID and Passkey are required.");
      return;
    }

    setLoading(true);

    /*
     * FRONTEND-ONLY AUTHENTICATION
     *
     * This is temporary for frontend development.
     * Later replace this section with the real backend
     * authentication API.
     */
    setTimeout(() => {
      const authData = {
        authenticated: true,
        name: name.trim(),
        agentId: agentId.trim(),
        role,
      };

      /*
       * Always clear both storage locations first.
       * This prevents an older login from overriding
       * the newly authenticated user's information.
       */
      sessionStorage.removeItem("tekmerion_auth");
      localStorage.removeItem("tekmerion_auth");

      const authStorage = rememberDevice
        ? localStorage
        : sessionStorage;

      authStorage.setItem(
        "tekmerion_auth",
        JSON.stringify(authData)
      );

      setLoading(false);

      navigate("/suspected", {
        replace: true,
      });
    }, 400);
  };

  return (
    <div className="login-page">
      <div className="login-workspace">
        <section className="login-hero">
          <div className="security-graphic">
            <svg
              viewBox="0 0 400 400"
              xmlns="http://www.w3.org/2000/svg"
              aria-hidden="true"
            >
              <path
                className="shield-outline"
                d="M200 40L60 100v120c0 106.67 114.67 194.67 140 213.33
                25.33-18.66 140-106.66 140-213.33V100L200 40z"
              />

              <path
                className="network-grid"
                d="M60 220h280M60 160h280M140 100v180M260 100v180"
              />

              <circle
                className="eye-ring"
                cx="200"
                cy="190"
                r="45"
              />

              <circle
                className="eye-core"
                cx="200"
                cy="190"
                r="15"
              />

              <circle
                className="orbit-node"
                cx="120"
                cy="130"
                r="8"
              />

              <circle
                className="orbit-node"
                cx="280"
                cy="250"
                r="6"
              />

              <circle
                className="orbit-node secondary"
                cx="140"
                cy="280"
                r="5"
              />

              <path
                className="connection-line"
                d="M120 130l80 60M280 250l-80-60"
              />
            </svg>
          </div>

          <div className="login-hero-copy">
            <h2>Fortifying Trust through Intelligent Vigilance.</h2>
            <p>
              Advanced threat detection and unified financial
              crime management system.
            </p>
          </div>
        </section>

        <section className="login-panel">
          <main className="login-container">
            <div className="login-brand">
              <div className="login-shield">
                <span className="material-symbols-outlined">
                  shield
                </span>
              </div>

              <h1>Tekmerion Intelligence</h1>
              <p>Financial Crime Unit Authentication</p>
            </div>

            <div className="login-card">
              <h2>Secure Access</h2>

              <form onSubmit={handleSubmit}>
                <div className="form-group">
                  <label className="form-label">
                    Authorization Level
                  </label>

                  <div className="role-selector">
                    <label
                      className={`role-option ${role === "junior" ? "active" : ""
                        }`}
                    >
                      <input
                        type="radio"
                        name="role"
                        value="junior"
                        checked={role === "junior"}
                        onChange={(event) =>
                          setRole(event.target.value)
                        }
                      />
                      <span>Junior Investigator</span>
                    </label>

                    <label
                      className={`role-option ${role === "senior" ? "active" : ""
                        }`}
                    >
                      <input
                        type="radio"
                        name="role"
                        value="senior"
                        checked={role === "senior"}
                        onChange={(event) =>
                          setRole(event.target.value)
                        }
                      />
                      <span>Senior Investigator</span>
                    </label>
                  </div>
                </div>

                <div className="credential-group">
                  <div className="credential-field">
                    <span className="material-symbols-outlined">
                      person
                    </span>

                    <input
                      type="text"
                      placeholder="Full Name"
                      value={name}
                      onChange={(event) =>
                        setName(event.target.value)
                      }
                      autoComplete="name"
                    />
                  </div>

                  <div className="credential-field">
                    <span className="material-symbols-outlined">
                      badge
                    </span>

                    <input
                      type="text"
                      placeholder="Agent ID"
                      value={agentId}
                      onChange={(event) =>
                        setAgentId(event.target.value)
                      }
                      autoComplete="username"
                    />
                  </div>

                  <div className="credential-field">
                    <span className="material-symbols-outlined">
                      key
                    </span>

                    <input
                      type="password"
                      placeholder="Passkey"
                      value={passkey}
                      onChange={(event) =>
                        setPasskey(event.target.value)
                      }
                      autoComplete="current-password"
                    />
                  </div>
                </div>

                <div className="login-options">
                  <label className="remember-device">
                    <input
                      type="checkbox"
                      checked={rememberDevice}
                      onChange={(event) =>
                        setRememberDevice(event.target.checked)
                      }
                    />
                    <span>Remember device</span>
                  </label>

                  <button
                    type="button"
                    className="recover-button"
                    onClick={() =>
                      setError(
                        "Access recovery is not connected yet."
                      )
                    }
                  >
                    Recover Access
                  </button>
                </div>

                {error && (
                  <div className="login-error">{error}</div>
                )}

                <button
                  type="submit"
                  className="authenticate-button"
                  disabled={loading}
                >
                  <span>
                    {loading ? "Authenticating..." : "Authenticate"}
                  </span>

                  {!loading && (
                    <span className="material-symbols-outlined">
                      arrow_forward
                    </span>
                  )}
                </button>
              </form>
            </div>

            <div className="login-footer">
              <span className="material-symbols-outlined">
                lock
              </span>

              <p>
                End-to-end encrypted connection.
                Authorized personnel only.
              </p>
            </div>
          </main>
        </section>
      </div>
    </div>
  );
}

export default Login;