Output scene-3-burn-ai.gif

Set Shell zsh
Set FontSize 15
Set Width 1000
Set Height 560
Set Padding 20
Set Theme "Catppuccin Mocha"
Set TypingSpeed 30ms

Type "burnkit status --refresh"
Sleep 500ms
Enter
Sleep 8s

Type "burnkit burn-ai menubar render > /tmp/menubar.png"
Sleep 500ms
Enter
Sleep 3s

Type "file /tmp/menubar.png"
Sleep 400ms
Enter
Sleep 3s
