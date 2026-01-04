/** @type {import('tailwindcss').Config} */
module.exports = {
    content: ["./app/templates/**/*.html", "./app/assets/css/styles.css"],
    theme: {
        extend: {
            fontFamily: {
                mono: ['"Space Mono"', "monospace"],
            },
            colors: {
                racing: {
                    red: "#DC2626",
                    black: "#000000",
                    paper: "#f0f0f0",
                },
            },
            boxShadow: {
                neo: "4px 4px 0px 0px rgba(0,0,0,1)",
                "neo-sm": "2px 2px 0px 0px rgba(0,0,0,1)",
            },
        },
    },
    plugins: [],
};
