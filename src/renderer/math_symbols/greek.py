"""希腊字母映射表。"""

from __future__ import annotations

from typing import Dict

_GREEK_LETTERS: Dict[str, str] = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ",
    "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ", "eta": "η",
    "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ",
    "varkappa": "ϰ", "lambda": "λ", "mu": "μ", "nu": "ν",
    "xi": "ξ", "omicron": "ο", "pi": "π", "varpi": "ϖ",
    "rho": "ρ", "varrho": "ϱ", "sigma": "σ", "varsigma": "ς",
    "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "φ",
    "chi": "χ", "psi": "ψ", "omega": "ω",
    "digamma": "ϝ",
    "backepsilon": "∍",
    "Alpha": "Α", "Beta": "Β", "Gamma": "Γ", "Delta": "Δ",
    "Epsilon": "Ε", "Zeta": "Ζ", "Eta": "Η", "Theta": "Θ",
    "Iota": "Ι", "Kappa": "Κ", "Lambda": "Λ", "Mu": "Μ",
    "Nu": "Ν", "Xi": "Ξ", "Omicron": "Ο", "Pi": "Π",
    "Rho": "Ρ", "Sigma": "Σ", "Tau": "Τ", "Upsilon": "Υ",
    "Phi": "Φ", "Chi": "Χ", "Psi": "Ψ", "Omega": "Ω",
    "varGamma": "Γ", "varDelta": "Δ", "varTheta": "Θ", "varLambda": "Λ",
    "varXi": "Ξ", "varPi": "Π", "varSigma": "Σ", "varPhi": "Φ", "varPsi": "Ψ", "varOmega": "Ω",
    # === 第三批补充（KaTeX 大写变体与符号别名）===
    "varUpsilon": "ϒ",
    "thetasym": "ϑ",
    "Coppa": "Ϙ", "coppa": "ϙ", "varcoppa": "ϙ",
    "Koppa": "Ϟ", "koppa": "ϟ",
    "Stigma": "Ϛ", "stigma": "ϛ", "varstigma": "ϛ",
    "Sampi": "Ϡ", "sampi": "ϡ",
    "Digamma": "Ϝ",
}
