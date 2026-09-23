; AgentNexus Windows installer overrides.
;
; Default uninstall behavior keeps user data (deleteAppDataOnUninstall: false
; in package.json). This macro is the explicit "clean local data" path: an
; assisted uninstall asks once. Silent/updated uninstalls never prompt and
; always keep data, so automatic upgrades can never destroy a session.

!macro customUnInstall
  ${ifNot} ${Silent}
    MessageBox MB_YESNO|MB_ICONQUESTION "Keep AgentNexus chats, settings, and local server data?" IDYES keepAgentNexusData IDNO removeAgentNexusData
    Goto agentNexusDataCleanupDone
    removeAgentNexusData:
      RMDir /r "$APPDATA\AgentNexus"
      RMDir /r "$PROFILE\.agentnexus"
      DetailPrint "AgentNexus local user data was removed."
      Goto agentNexusDataCleanupDone
    keepAgentNexusData:
      DetailPrint "AgentNexus local user data was kept."
    agentNexusDataCleanupDone:
  ${endIf}
!macroend
